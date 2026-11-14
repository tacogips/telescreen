"""Path-only workflow template parser and renderer."""

from __future__ import annotations

from telescreen.core.json import JsonKind, JsonValue, dump_json, json_array
from telescreen.core.templating import Template as Template
from telescreen.core.templating import TemplatePart as TemplatePart
from telescreen.core.templating import parse_template as parse_template
from telescreen.workflow.dsl.context import RunContext


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
