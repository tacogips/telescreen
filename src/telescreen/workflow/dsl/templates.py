"""Path-only workflow template parser and renderer."""

from __future__ import annotations

import re

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, dump_json, json_array
from telescreen.core.vocabulary import ErrorCode
from telescreen.workflow.dsl.context import RunContext
from telescreen.workflow.dsl.model import Template, TemplatePart

_TEMPLATE_PATTERN: re.Pattern[str] = re.compile(r"{{\s*([^{}]+?)\s*}}")
_PATH_PATTERN: re.Pattern[str] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*$"
)


def parse_template(text: str) -> Template:
    """Parse literal text and simple context substitutions."""
    parts: list[TemplatePart] = []
    offset: int = 0
    for match in _TEMPLATE_PATTERN.finditer(text):
        if match.start() > offset:
            parts.append(TemplatePart(literal=text[offset : match.start()]))
        path_text: str = match.group(1).strip()
        if _PATH_PATTERN.fullmatch(path_text) is None:
            raise TelescreenError(ErrorCode.EXPRESSION_ERROR, "invalid template")
        parts.append(TemplatePart(path=tuple(path_text.split("."))))
        offset = match.end()
    remainder: str = text[offset:]
    if "{{" in remainder or "}}" in remainder:
        raise TelescreenError(ErrorCode.EXPRESSION_ERROR, "invalid template")
    if remainder:
        parts.append(TemplatePart(literal=remainder))
    return tuple(parts)


def render(template: Template, ctx: RunContext) -> str:
    """Render a template from masked context, with missing paths as empty text."""
    return "".join(
        part.literal if part.literal is not None else _render_value(ctx.lookup(part.path))
        for part in template
    )


def _render_value(value: JsonValue | None) -> str:
    if value is None or value.kind is JsonKind.NULL:
        return ""
    if value.kind is JsonKind.STRING:
        return value.str_value
    if value.kind in (JsonKind.INT, JsonKind.FLOAT, JsonKind.BOOL):
        return dump_json(value)
    if value.kind is JsonKind.ARRAY:
        return dump_json(json_array(value.items))
    return dump_json(value)
