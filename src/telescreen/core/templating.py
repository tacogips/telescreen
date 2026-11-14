"""Pure path-only notification and workflow template parser."""

from __future__ import annotations

import re
from dataclasses import dataclass

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode


@dataclass(frozen=True)
class TemplatePart:
    """Literal text or a context path in a parsed template."""

    literal: str | None = None
    path: tuple[str, ...] = ()


type Template = tuple[TemplatePart, ...]

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
