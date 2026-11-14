"""Safe workflow expression tokenizer, parser and evaluator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NoReturn

from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    json_array,
    json_bool,
    json_float,
    json_int,
    json_null,
    parse_json,
)
from telescreen.core.vocabulary import ErrorCode
from telescreen.workflow.dsl.context import RunContext
from telescreen.workflow.dsl.model import Expression


@dataclass(frozen=True)
class _Token:
    """A source token with its parser category."""

    kind: str
    value: str


_TOKEN_PATTERN: re.Pattern[str] = re.compile(
    r'\s+|==|!=|<=|>=|[()\[\],<>]|"(?:\\.|[^"\\])*"|'
    r"'(?:\\.|[^'\\])*'|[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|"
    r"[A-Za-z_][A-Za-z0-9_-]*|\."
)


def _single_quoted_json_text(token: str) -> str:
    """Convert a single-quoted expression token to JSON string text."""
    content: str = token[1:-1]
    encoded: list[str] = ['"']
    index: int = 0
    while index < len(content):
        character: str = content[index]
        if character == "\\" and index + 1 < len(content):
            escaped: str = content[index + 1]
            if escaped == "'":
                encoded.append("'")
            else:
                encoded.extend(("\\", escaped))
            index += 2
            continue
        encoded.append('\\"' if character == '"' else character)
        index += 1
    encoded.append('"')
    return "".join(encoded)


@dataclass
class _Parser:
    """Recursive-descent parser for the workflow expression grammar."""

    tokens: tuple[_Token, ...]
    position: int

    def __init__(self, text: str) -> None:
        """Tokenize source without evaluating it."""
        tokens: list[_Token] = []
        offset: int = 0
        while offset < len(text):
            match: re.Match[str] | None = _TOKEN_PATTERN.match(text, offset)
            if match is None:
                raise TelescreenError(ErrorCode.EXPRESSION_ERROR, "invalid expression")
            value: str = match.group(0)
            offset = match.end()
            if value.isspace():
                continue
            if value[0] in "\"'":
                tokens.append(_Token("string", value))
            elif value[0].isdigit() or value[0] in "+-.":
                tokens.append(_Token("number", value))
            elif value[0].isalpha() or value[0] == "_":
                tokens.append(_Token("word", value))
            else:
                tokens.append(_Token("symbol", value))
        tokens.append(_Token("end", ""))
        self.tokens = tuple(tokens)
        self.position = 0

    def parse(self) -> Expression:
        """Parse the complete expression, rejecting trailing source."""
        expression: Expression = self._or()
        if self._peek().kind != "end":
            self._fail()
        return expression

    def _or(self) -> Expression:
        left: Expression = self._and()
        while self._word("or"):
            left = Expression("binary", path=("or",), children=(left, self._and()))
        return left

    def _and(self) -> Expression:
        left: Expression = self._not()
        while self._word("and"):
            left = Expression("binary", path=("and",), children=(left, self._not()))
        return left

    def _not(self) -> Expression:
        if self._word("not"):
            return Expression("unary", path=("not",), children=(self._not(),))
        return self._compare()

    def _compare(self) -> Expression:
        left: Expression = self._primary()
        operator: _Token = self._peek()
        if operator.kind == "word" and operator.value == "not":
            self.position += 1
            if not self._word("in"):
                self._fail()
            return Expression("binary", path=("not in",), children=(left, self._primary()))
        if operator.value in ("==", "!=", "<", "<=", ">", ">=") or (
            operator.kind == "word" and operator.value == "in"
        ):
            self.position += 1
            return Expression("binary", path=(operator.value,), children=(left, self._primary()))
        return left

    def _primary(self) -> Expression:
        token: _Token = self._peek()
        if token.value == "(":
            self.position += 1
            expression: Expression = self._or()
            self._expect(")")
            return expression
        if token.value == "[":
            self.position += 1
            items: list[Expression] = []
            if self._peek().value != "]":
                items.append(self._or())
                while self._accept(","):
                    items.append(self._or())
            self._expect("]")
            return Expression("list", children=tuple(items))
        if token.kind == "string":
            self.position += 1
            json_text: str = (
                token.value
                if token.value.startswith('"')
                else _single_quoted_json_text(token.value)
            )
            try:
                literal: JsonValue = parse_json(json_text)
            except TelescreenError:
                self._fail()
            return Expression("literal", value=literal)
        if token.kind == "number":
            self.position += 1
            is_float: bool = any(marker in token.value for marker in (".", "e", "E"))
            try:
                value: JsonValue = (
                    json_float(float(token.value)) if is_float else json_int(int(token.value))
                )
            except (ValueError, TelescreenError):
                self._fail()
            return Expression("literal", value=value)
        if token.kind == "word" and token.value in ("true", "false", "null"):
            self.position += 1
            value: JsonValue = (
                json_bool(token.value == "true") if token.value != "null" else json_null()
            )
            return Expression("literal", value=value)
        if token.kind == "word":
            return Expression("path", path=self._path())
        self._fail()

    def _path(self) -> tuple[str, ...]:
        parts: list[str] = [self._take_word()]
        while self._accept("."):
            parts.append(self._take_word())
        return tuple(parts)

    def _peek(self) -> _Token:
        return self.tokens[self.position]

    def _word(self, value: str) -> bool:
        token: _Token = self._peek()
        if token.kind == "word" and token.value == value:
            self.position += 1
            return True
        return False

    def _take_word(self) -> str:
        token: _Token = self._peek()
        if token.kind != "word":
            self._fail()
        self.position += 1
        return token.value

    def _accept(self, value: str) -> bool:
        if self._peek().value == value:
            self.position += 1
            return True
        return False

    def _expect(self, value: str) -> None:
        if not self._accept(value):
            self._fail()

    def _fail(self) -> NoReturn:
        raise TelescreenError(ErrorCode.EXPRESSION_ERROR, "invalid expression")


def parse_expression(text: str) -> Expression:
    """Parse an expression without executing Python source."""
    return _Parser(text).parse()


@dataclass(frozen=True)
class EvalResult:
    """Expression value and an optional safe diagnostic note."""

    value: JsonValue
    note: str | None = None


def evaluate(expr: Expression, ctx: RunContext) -> EvalResult:
    """Evaluate a parsed expression against masked workflow context."""
    if expr.kind == "literal":
        return EvalResult(expr.value or json_null())
    if expr.kind == "path":
        return EvalResult(ctx.lookup(expr.path) or json_null())
    if expr.kind == "list":
        return EvalResult(json_array(tuple(evaluate(item, ctx).value for item in expr.children)))
    if expr.kind == "unary":
        result: EvalResult = evaluate(expr.children[0], ctx)
        return EvalResult(json_bool(not _truthy(result.value)), result.note)
    operator: str = expr.path[0]
    left: EvalResult = evaluate(expr.children[0], ctx)
    if operator == "and" and not _truthy(left.value):
        return EvalResult(json_bool(False), left.note)
    if operator == "or" and _truthy(left.value):
        return EvalResult(json_bool(True), left.note)
    right: EvalResult = evaluate(expr.children[1], ctx)
    if operator in ("and", "or"):
        return EvalResult(json_bool(_truthy(right.value)), left.note or right.note)
    if operator == "in":
        return EvalResult(
            json_bool(right.value.kind is JsonKind.ARRAY and left.value in right.value.items)
        )
    if operator == "not in":
        return EvalResult(
            json_bool(right.value.kind is not JsonKind.ARRAY or left.value not in right.value.items)
        )
    if operator in ("==", "!="):
        if left.value.kind is not right.value.kind:
            return EvalResult(json_bool(False), "type_mismatch")
        return EvalResult(json_bool((left.value == right.value) == (operator == "==")))
    compared: bool | None = _compare(left.value, right.value, operator)
    return EvalResult(
        json_bool(compared is True), None if compared is not None else "type_mismatch"
    )


def _truthy(value: JsonValue) -> bool:
    if value.kind is JsonKind.BOOL:
        return value.bool_value
    return value.kind is not JsonKind.NULL


def _compare(left: JsonValue, right: JsonValue, operator: str) -> bool | None:
    if left.kind is JsonKind.STRING and right.kind is JsonKind.STRING:
        first: str = left.str_value
        second: str = right.str_value
    elif left.as_float() is not None and right.as_float() is not None:
        first = str(left.as_float())
        second = str(right.as_float())
    else:
        return None
    if operator == "<":
        return (
            first < second
            if isinstance(left.as_float(), type(None))
            else float(first) < float(second)
        )
    if operator == "<=":
        return (
            first <= second
            if isinstance(left.as_float(), type(None))
            else float(first) <= float(second)
        )
    if operator == ">":
        return (
            first > second
            if isinstance(left.as_float(), type(None))
            else float(first) > float(second)
        )
    return (
        first >= second
        if isinstance(left.as_float(), type(None))
        else float(first) >= float(second)
    )
