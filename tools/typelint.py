"""AST-based type-discipline linter for the telescreen code base.

The project forbids untyped or loosely typed Python. Ruff and ty cover part of
the rules, but several project-specific rules can only be enforced by walking
the syntax tree:

TL000  file cannot be parsed
TL001  function parameter without a type annotation
TL002  function without a return type annotation
TL003  variable bound without a type annotation (``x = 1`` instead of ``x: int = 1``).
       Exempt: enum members, TypeVar/NewType factories, and dunder attributes assigned in
       a class body (``__tablename__``, ``__table_args__``, ``__slots__``): their type is
       declared by the base class or the data model, and re-annotating them makes ty
       report an invalid override or turns them into dataclass fields.
TL004  ``self.attr = ...`` where ``attr`` is not declared with a type in the class body
TL005  forbidden type (``Any``, ``object``, ``dict`` and other mapping types)
TL006  ``**kwargs`` parameter in a function definition
TL007  ``**mapping`` unpacking in a call
TL008  class that declares typed fields without being a dataclass, enum or other allowed base
TL009  bare generic type without type arguments (implicit ``Any``), or ``Callable[..., T]``
TL010  type-checker suppression comment (``type: ignore``, ``ty: ignore``, ``pyright: ignore``)

There are deliberately no inline suppression comments. The only escape hatch is
``[tool.typelint.per-file-ignores]`` in ``pyproject.toml`` so every exemption is
visible in review.

Usage::

    uv run python tools/typelint.py            # paths from [tool.typelint].paths
    uv run python tools/typelint.py src/foo.py # explicit files or directories
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize
import tomllib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class Rule(StrEnum):
    """Lint rule identifiers."""

    PARSE_ERROR = "TL000"
    MISSING_PARAM_ANNOTATION = "TL001"
    MISSING_RETURN_ANNOTATION = "TL002"
    UNANNOTATED_VARIABLE = "TL003"
    UNDECLARED_INSTANCE_ATTRIBUTE = "TL004"
    FORBIDDEN_TYPE = "TL005"
    FORBIDDEN_KWARGS_PARAMETER = "TL006"
    FORBIDDEN_KWARGS_UNPACK = "TL007"
    CLASS_NOT_DATACLASS = "TL008"
    BARE_GENERIC = "TL009"
    TYPE_SUPPRESSION_COMMENT = "TL010"


FORBIDDEN_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "Any",
        "object",
        "dict",
        "Dict",
        "DefaultDict",
        "defaultdict",
        "OrderedDict",
        "Mapping",
        "MutableMapping",
        "TypedDict",
    }
)

# Builtins that are forbidden in annotations but may still be used as runtime
# values (``isinstance(value, dict)``), so importing them is not an error.
RUNTIME_ONLY_FORBIDDEN_NAMES: frozenset[str] = frozenset({"dict", "object"})

BARE_GENERIC_NAMES: frozenset[str] = frozenset(
    {
        "list",
        "List",
        "set",
        "Set",
        "frozenset",
        "FrozenSet",
        "tuple",
        "Tuple",
        "type",
        "Type",
        "Callable",
        "Sequence",
        "MutableSequence",
        "Iterable",
        "Iterator",
        "AsyncIterable",
        "AsyncIterator",
        "Generator",
        "AsyncGenerator",
        "Awaitable",
        "Coroutine",
        "Collection",
        "AbstractSet",
        "MutableSet",
        "deque",
        "Deque",
    }
)

ENUM_BASE_NAMES: frozenset[str] = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})

ROOT_ALLOWED_BASES: frozenset[str] = ENUM_BASE_NAMES | frozenset(
    {"Protocol", "BaseException", "Exception", "Warning", "MappedAsDataclass"}
)

EXCEPTION_NAME_SUFFIXES: tuple[str, ...] = ("Error", "Exception", "Warning")

DATACLASS_DECORATOR_NAMES: frozenset[str] = frozenset({"dataclass", "mapped_as_dataclass"})

# Subscripts whose arguments are values, not types (only the first ``Annotated``
# argument is a type; ``Literal`` holds values only).
LITERAL_SUBSCRIPT: str = "Literal"
ANNOTATED_SUBSCRIPT: str = "Annotated"

UNANNOTATABLE_FACTORY_CALLS: frozenset[str] = frozenset(
    {"TypeVar", "ParamSpec", "TypeVarTuple", "NewType"}
)

THROWAWAY_NAME: str = "_"

SUPPRESSION_MARKERS: tuple[str, ...] = ("type: ignore", "ty: ignore", "pyright: ignore")


@dataclass(frozen=True)
class Violation:
    """A single lint violation."""

    path: Path
    line: int
    column: int
    rule: Rule
    message: str

    def render(self) -> str:
        """Render the violation in the conventional ``path:line:col`` format."""
        return f"{self.path}:{self.line}:{self.column + 1}: {self.rule.value} {self.message}"


@dataclass(frozen=True)
class PerFileIgnore:
    """Rules ignored for one file, configured in ``pyproject.toml``."""

    path: str
    rules: frozenset[Rule]


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


@dataclass(frozen=True)
class Config:
    """Linter configuration loaded from ``[tool.typelint]``."""

    paths: tuple[str, ...] = ("src", "tests", "tools")
    exclude: tuple[str, ...] = ()
    extra_allowed_bases: frozenset[str] = frozenset()
    per_file_ignores: tuple[PerFileIgnore, ...] = ()

    def ignored_rules(self, path: Path, root: Path) -> frozenset[Rule]:
        """Return the rules ignored for ``path``."""
        relative: str = _relative_posix(path, root)
        rules: set[Rule] = set()
        for entry in self.per_file_ignores:
            if relative == entry.path:
                rules.update(entry.rules)
        return frozenset(rules)

    def is_excluded(self, path: Path, root: Path) -> bool:
        """Return whether ``path`` lies under an excluded path prefix."""
        relative: str = _relative_posix(path, root)
        return any(
            relative == prefix or relative.startswith(prefix.rstrip("/") + "/")
            for prefix in self.exclude
        )


# TOML boundary -------------------------------------------------------------------------


class TomlTable(Protocol):
    """Structural view of a parsed TOML table (``tomllib`` returns plain mappings)."""

    def get(self, key: str, /) -> TomlTable | list[str] | str | int | float | bool | None: ...

    def items(self) -> Iterable[tuple[str, TomlTable | list[str] | str | int | float | bool]]: ...


type TomlValue = TomlTable | list[str] | str | int | float | bool


def _as_table(value: TomlValue | None) -> TomlTable | None:
    if value is None or isinstance(value, str | int | float | bool | list):
        return None
    return value


def _string_tuple(value: TomlValue | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return default


def config_from_table(document: TomlTable) -> Config:
    """Build a :class:`Config` from a parsed ``pyproject.toml`` document."""
    tool: TomlTable | None = _as_table(document.get("tool"))
    section: TomlTable | None = _as_table(tool.get("typelint")) if tool is not None else None
    if section is None:
        return Config()
    ignores: list[PerFileIgnore] = []
    ignore_table: TomlTable | None = _as_table(section.get("per-file-ignores"))
    if ignore_table is not None:
        for key, value in ignore_table.items():
            rules: frozenset[Rule] = frozenset(Rule(code) for code in _string_tuple(value, ()))
            ignores.append(PerFileIgnore(path=key, rules=rules))
    return Config(
        paths=_string_tuple(section.get("paths"), Config.paths),
        exclude=_string_tuple(section.get("exclude"), ()),
        extra_allowed_bases=frozenset(_string_tuple(section.get("extra-allowed-bases"), ())),
        per_file_ignores=tuple(ignores),
    )


def load_config(pyproject: Path) -> Config:
    """Load ``[tool.typelint]`` from ``pyproject.toml``; defaults when absent."""
    if not pyproject.is_file():
        return Config()
    with pyproject.open("rb") as handle:
        return config_from_table(tomllib.load(handle))


# Class registry ------------------------------------------------------------------------


def _terminal_name(node: ast.expr) -> str | None:
    """Return the last dotted component of a ``Name`` or ``Attribute`` expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_dunder(name: str) -> bool:
    """Return whether ``name`` is a dunder such as ``__tablename__`` or ``__slots__``."""
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _decorator_is_dataclass(decorator: ast.expr) -> bool:
    target: ast.expr = decorator.func if isinstance(decorator, ast.Call) else decorator
    return _terminal_name(target) in DATACLASS_DECORATOR_NAMES


def _class_base_names(node: ast.ClassDef) -> tuple[str, ...]:
    names: list[str] = []
    for base in node.bases:
        target: ast.expr = base.value if isinstance(base, ast.Subscript) else base
        name: str | None = _terminal_name(target)
        if name is not None:
            names.append(name)
    return tuple(names)


@dataclass(frozen=True)
class ClassInfo:
    """Facts about a class definition collected in the first pass."""

    name: str
    base_names: tuple[str, ...]


def collect_classes(tree: ast.Module) -> list[ClassInfo]:
    """Collect class facts from a module for cross-file base resolution."""
    return [
        ClassInfo(name=node.name, base_names=_class_base_names(node))
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]


@dataclass
class ClassRegistry:
    """Class facts across every linted file, used to resolve bases by simple name."""

    classes: list[ClassInfo] = field(default_factory=list)
    extra_allowed_bases: frozenset[str] = frozenset()

    def ancestors(self, base_names: Sequence[str]) -> set[str]:
        """Return every base name reachable from ``base_names`` (by simple name)."""
        resolved: set[str] = set()
        pending: list[str] = list(base_names)
        while pending:
            name: str = pending.pop()
            if name in resolved:
                continue
            resolved.add(name)
            for info in self.classes:
                if info.name == name:
                    pending.extend(info.base_names)
        return resolved

    def derives_from_allowed_base(self, base_names: Sequence[str]) -> bool:
        """Return whether a base resolves to an enum, protocol, exception or allowed base."""
        allowed: frozenset[str] = ROOT_ALLOWED_BASES | self.extra_allowed_bases
        return any(
            name in allowed or name.endswith(EXCEPTION_NAME_SUFFIXES)
            for name in self.ancestors(base_names)
        )

    def derives_from_enum(self, base_names: Sequence[str]) -> bool:
        """Return whether a base resolves to an enum type."""
        return bool(self.ancestors(base_names) & ENUM_BASE_NAMES)


# Annotation checking -------------------------------------------------------------------


@dataclass
class AnnotationChecker:
    """Checks type expressions for forbidden and bare generic types."""

    path: Path
    sink: list[Violation]

    def check(self, annotation: ast.expr) -> None:
        """Check a whole annotation expression."""
        self._visit(annotation, subscripted=False)

    def _report(self, node: ast.expr, rule: Rule, message: str) -> None:
        self.sink.append(Violation(self.path, node.lineno, node.col_offset, rule, message))

    def _visit(self, node: ast.expr, subscripted: bool) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            self._visit_string(node)
        elif isinstance(node, ast.Subscript):
            self._visit_subscript(node)
        elif isinstance(node, ast.Name | ast.Attribute):
            self._visit_name_like(node, subscripted=subscripted)
        else:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self._visit(child, subscripted=False)

    def _visit_subscript(self, node: ast.Subscript) -> None:
        name: str | None = _terminal_name(node.value)
        self._visit_name_like(node.value, subscripted=True)
        if name == LITERAL_SUBSCRIPT:
            return
        if name == ANNOTATED_SUBSCRIPT:
            if isinstance(node.slice, ast.Tuple) and node.slice.elts:
                self._visit(node.slice.elts[0], subscripted=False)
            return
        self._check_callable_ellipsis(node)
        self._visit(node.slice, subscripted=False)

    def _visit_name_like(self, node: ast.expr, subscripted: bool) -> None:
        name: str | None = _terminal_name(node)
        if name is None:
            return
        if name in FORBIDDEN_TYPE_NAMES:
            self._report(
                node,
                Rule.FORBIDDEN_TYPE,
                f"forbidden type `{name}`; define a dataclass, enum or protocol instead",
            )
        elif name in BARE_GENERIC_NAMES and not subscripted:
            self._report(node, Rule.BARE_GENERIC, f"bare generic `{name}` needs type arguments")

    def _check_callable_ellipsis(self, node: ast.Subscript) -> None:
        if _terminal_name(node.value) != "Callable":
            return
        if not isinstance(node.slice, ast.Tuple) or not node.slice.elts:
            return
        first: ast.expr = node.slice.elts[0]
        if isinstance(first, ast.Constant) and first.value is Ellipsis:
            self._report(
                node,
                Rule.BARE_GENERIC,
                "`Callable[..., T]` accepts arbitrary arguments; "
                "use explicit parameter types or a ParamSpec",
            )

    def _visit_string(self, node: ast.Constant) -> None:
        try:
            parsed: ast.Expression = ast.parse(str(node.value), mode="eval")
        except SyntaxError:
            return
        ast.increment_lineno(parsed, node.lineno - 1)
        self._visit(parsed.body, subscripted=False)


# Module checking -----------------------------------------------------------------------


@dataclass
class Scope:
    """Names known to be declared in the current lexical scope."""

    declared: set[str] = field(default_factory=set)
    class_fields: frozenset[str] | None = None
    is_enum_class: bool = False


@dataclass(frozen=True)
class SelfContext:
    """The implicit ``self`` parameter name and declared fields of the enclosing class."""

    self_name: str | None
    class_fields: frozenset[str] | None


@dataclass
class ModuleChecker(ast.NodeVisitor):
    """Single-module checker for every rule except the comment-based TL010."""

    path: Path
    registry: ClassRegistry
    violations: list[Violation] = field(default_factory=list)
    scopes: list[Scope] = field(default_factory=lambda: [Scope()])
    self_contexts: list[SelfContext] = field(default_factory=list)
    annotations: AnnotationChecker = field(init=False)

    def __post_init__(self) -> None:
        self.annotations = AnnotationChecker(self.path, self.violations)

    @property
    def scope(self) -> Scope:
        """The innermost lexical scope."""
        return self.scopes[-1]

    def _report(self, node: ast.expr | ast.stmt | ast.arg, rule: Rule, message: str) -> None:
        self.violations.append(Violation(self.path, node.lineno, node.col_offset, rule, message))

    # Imports and simple declarations -------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.scope.declared.add((alias.asname or alias.name).split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in FORBIDDEN_TYPE_NAMES - RUNTIME_ONLY_FORBIDDEN_NAMES:
                self._report(node, Rule.FORBIDDEN_TYPE, f"importing forbidden type `{alias.name}`")
            self.scope.declared.add(alias.asname or alias.name)

    def visit_Global(self, node: ast.Global) -> None:
        self.scope.declared.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.scope.declared.update(node.names)

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        self.scope.declared.add(node.name.id)
        self.annotations.check(node.value)

    # Functions -----------------------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.declared.add(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        class_fields: frozenset[str] | None = self.scope.class_fields
        is_static: bool = any(_terminal_name(d) == "staticmethod" for d in node.decorator_list)
        arguments: ast.arguments = node.args
        positional: list[ast.arg] = [*arguments.posonlyargs, *arguments.args]
        implicit_first: str | None = None
        if class_fields is not None and not is_static and positional:
            implicit_first = positional[0].arg
        named: list[ast.arg] = [*positional, *arguments.kwonlyargs]
        for argument in named:
            if argument.arg != implicit_first:
                self._check_argument(argument, node.name)
        if arguments.vararg is not None:
            self._check_argument(arguments.vararg, node.name)
        if arguments.kwarg is not None:
            self._report(
                arguments.kwarg,
                Rule.FORBIDDEN_KWARGS_PARAMETER,
                f"`**{arguments.kwarg.arg}` is forbidden in `{node.name}`; "
                "declare explicit keyword parameters",
            )
        if node.returns is None:
            self._report(
                node, Rule.MISSING_RETURN_ANNOTATION, f"function `{node.name}` has no return type"
            )
        else:
            self.annotations.check(node.returns)
        for default in [*arguments.defaults, *arguments.kw_defaults]:
            if default is not None:
                self.visit(default)

        function_scope: Scope = Scope(declared={argument.arg for argument in named})
        for extra in (arguments.vararg, arguments.kwarg):
            if extra is not None:
                function_scope.declared.add(extra.arg)
        self.scopes.append(function_scope)
        self.self_contexts.append(SelfContext(implicit_first, class_fields))
        for statement in node.body:
            self.visit(statement)
        self.self_contexts.pop()
        self.scopes.pop()

    def _check_argument(self, argument: ast.arg, function_name: str) -> None:
        if argument.annotation is None:
            self._report(
                argument,
                Rule.MISSING_PARAM_ANNOTATION,
                f"parameter `{argument.arg}` of `{function_name}` has no type annotation",
            )
        else:
            self.annotations.check(argument.annotation)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        if node.args.kwarg is not None:
            self._report(node, Rule.FORBIDDEN_KWARGS_PARAMETER, "`**kwargs` is forbidden")
        self.visit(node.body)

    # Classes -------------------------------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.declared.add(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            base_name: str | None = _terminal_name(base)
            if base_name in FORBIDDEN_TYPE_NAMES:
                self._report(base, Rule.FORBIDDEN_TYPE, f"class base `{base_name}` is forbidden")
        base_names: tuple[str, ...] = _class_base_names(node)
        is_dataclass: bool = any(_decorator_is_dataclass(d) for d in node.decorator_list)
        fields: frozenset[str] = frozenset(
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        )
        if fields and not is_dataclass and not self.registry.derives_from_allowed_base(base_names):
            self._report(
                node,
                Rule.CLASS_NOT_DATACLASS,
                f"class `{node.name}` declares typed fields but is not a dataclass or enum",
            )
        self.scopes.append(
            Scope(
                class_fields=fields,
                is_enum_class=self.registry.derives_from_enum(base_names),
            )
        )
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    # Assignments ---------------------------------------------------------------------

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.annotations.check(node.annotation)
        if isinstance(node.target, ast.Name):
            self.scope.declared.add(node.target.id)
        elif isinstance(node.target, ast.Attribute):
            self._check_self_attribute(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        exempt: bool = self._is_exempt_assignment(node)
        for target in node.targets:
            self._check_assignment_target(target, exempt)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        if isinstance(node.target, ast.Attribute):
            self._check_self_attribute(node.target)

    def _is_exempt_assignment(self, node: ast.Assign) -> bool:
        if self.scope.is_enum_class:
            return True
        if self.scope.class_fields is not None and all(
            isinstance(target, ast.Name) and _is_dunder(target.id) for target in node.targets
        ):
            return True
        if isinstance(node.value, ast.Call):
            return _terminal_name(node.value.func) in UNANNOTATABLE_FACTORY_CALLS
        return False

    def _check_assignment_target(self, target: ast.expr, exempt: bool) -> None:
        if isinstance(target, ast.Name):
            undeclared: bool = target.id not in self.scope.declared
            if undeclared and not exempt and target.id != THROWAWAY_NAME:
                self._report(
                    target,
                    Rule.UNANNOTATED_VARIABLE,
                    f"`{target.id}` is bound without a type annotation; "
                    f"write `{target.id}: <type> = ...`",
                )
            self.scope.declared.add(target.id)
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                inner: ast.expr = element.value if isinstance(element, ast.Starred) else element
                self._check_unpacked_target(inner, exempt)
        elif isinstance(target, ast.Attribute):
            self._check_self_attribute(target)
            self.visit(target.value)
        else:
            self.visit(target)

    def _check_unpacked_target(self, target: ast.expr, exempt: bool) -> None:
        if not isinstance(target, ast.Name):
            self._check_assignment_target(target, exempt)
            return
        if target.id not in self.scope.declared and target.id != THROWAWAY_NAME:
            self._report(
                target,
                Rule.UNANNOTATED_VARIABLE,
                f"`{target.id}` is unpacked without a prior typed declaration; "
                f"declare `{target.id}: <type>` first",
            )
        self.scope.declared.add(target.id)

    def _check_self_attribute(self, target: ast.Attribute) -> None:
        if not self.self_contexts:
            return
        context: SelfContext = self.self_contexts[-1]
        if context.self_name is None or context.class_fields is None:
            return
        if not isinstance(target.value, ast.Name) or target.value.id != context.self_name:
            return
        if target.attr not in context.class_fields:
            self._report(
                target,
                Rule.UNDECLARED_INSTANCE_ATTRIBUTE,
                f"`{context.self_name}.{target.attr}` is not declared with a type "
                "in the class body",
            )

    # Bindings that cannot carry annotations ----------------------------------------

    def _declare_target(self, target: ast.expr) -> None:
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                self.scope.declared.add(node.id)

    def visit_For(self, node: ast.For) -> None:
        self._declare_target(node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._declare_target(node.target)
        self.generic_visit(node)

    def visit_withitem(self, node: ast.withitem) -> None:
        if node.optional_vars is not None:
            self._declare_target(node.optional_vars)
        self.visit(node.context_expr)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.scope.declared.add(node.name)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.scope.declared.add(node.target.id)
        self.visit(node.value)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self.scope.declared.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self.scope.declared.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self.scope.declared.add(node.rest)
        self.generic_visit(node)

    # Calls ---------------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg is None:
                self._report(
                    keyword.value,
                    Rule.FORBIDDEN_KWARGS_UNPACK,
                    "`**` unpacking in calls is forbidden; pass explicit keyword arguments",
                )
        if _terminal_name(node.func) == "cast" and node.args:
            self.annotations.check(node.args[0])
        self.generic_visit(node)


def suppression_comment_violations(path: Path, source: str) -> list[Violation]:
    """Return TL010 violations for type-checker suppression comments."""
    violations: list[Violation] = []
    try:
        tokens: list[tokenize.TokenInfo] = list(
            tokenize.generate_tokens(io.StringIO(source).readline)
        )
    except (tokenize.TokenError, SyntaxError):
        return violations
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        text: str = token.string.lstrip("#").strip()
        for marker in SUPPRESSION_MARKERS:
            if text.startswith(marker) or f" {marker}" in text or f"#{marker}" in text:
                violations.append(
                    Violation(
                        path,
                        token.start[0],
                        token.start[1],
                        Rule.TYPE_SUPPRESSION_COMMENT,
                        f"`{marker}` suppression comments are forbidden; fix the type instead",
                    )
                )
                break
    return violations


def check_tree(
    path: Path, source: str, tree: ast.Module, registry: ClassRegistry
) -> list[Violation]:
    """Run every rule over one parsed module."""
    checker: ModuleChecker = ModuleChecker(path=path, registry=registry)
    checker.visit(tree)
    return [*checker.violations, *suppression_comment_violations(path, source)]


def lint_source(source: str, path: Path = Path("<memory>")) -> list[Violation]:
    """Lint a single source string (used by tests)."""
    tree: ast.Module = ast.parse(source)
    return check_tree(path, source, tree, ClassRegistry(classes=collect_classes(tree)))


@dataclass(frozen=True)
class ParsedModule:
    """A source file with its parsed syntax tree."""

    path: Path
    source: str
    tree: ast.Module


def iter_python_files(targets: Sequence[Path], config: Config, root: Path) -> Iterator[Path]:
    """Yield Python files under ``targets`` that are not excluded."""
    skipped_parts: frozenset[str] = frozenset({".venv", "node_modules", "__pycache__"})
    for target in targets:
        candidates: list[Path] = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for candidate in candidates:
            if candidate.suffix != ".py" or config.is_excluded(candidate, root):
                continue
            if skipped_parts.isdisjoint(candidate.parts):
                yield candidate


def lint_files(files: Sequence[Path], config: Config, root: Path) -> list[Violation]:
    """Lint ``files`` and return violations not ignored by configuration."""
    modules: list[ParsedModule] = []
    violations: list[Violation] = []
    registry: ClassRegistry = ClassRegistry(extra_allowed_bases=config.extra_allowed_bases)
    for path in files:
        source: str = path.read_text(encoding="utf-8")
        try:
            tree: ast.Module = ast.parse(source, filename=str(path))
        except SyntaxError as error:
            violations.append(
                Violation(
                    path, error.lineno or 1, 0, Rule.PARSE_ERROR, f"cannot parse: {error.msg}"
                )
            )
            continue
        registry.classes.extend(collect_classes(tree))
        modules.append(ParsedModule(path, source, tree))
    for module in modules:
        ignored: frozenset[Rule] = config.ignored_rules(module.path, root)
        violations.extend(
            violation
            for violation in check_tree(module.path, module.source, module.tree, registry)
            if violation.rule not in ignored
        )
    violations.sort(key=lambda v: (str(v.path), v.line, v.column, v.rule.value))
    return violations


def main(argv: Sequence[str]) -> int:
    """Command-line entry point; returns the process exit code."""
    root: Path = Path.cwd()
    config: Config = load_config(root / "pyproject.toml")
    targets: list[Path] = [Path(arg) for arg in argv] or [root / p for p in config.paths]
    files: list[Path] = list(iter_python_files([t for t in targets if t.exists()], config, root))
    violations: list[Violation] = lint_files(files, config, root)
    for violation in violations:
        print(violation.render())
    if violations:
        print(f"typelint: {len(violations)} violation(s) in {len(files)} file(s)", file=sys.stderr)
        return 1
    print(f"typelint: {len(files)} file(s) clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
