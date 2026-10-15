"""Pure ownership-rule matching independent of SQLAlchemy models."""

import re
from collections.abc import Sequence
from typing import Protocol


class RuleLike(Protocol):
    """Read-only fields needed to evaluate one ownership rule."""

    @property
    def id(self) -> str: ...

    @property
    def position(self) -> int: ...

    @property
    def source(self) -> str | None: ...

    @property
    def pattern(self) -> str: ...

    @property
    def team_id(self) -> str: ...

    @property
    def enabled(self) -> bool: ...


def match_rule[R: RuleLike](rules: Sequence[R], source: str, locator: str) -> R | None:
    """Return the first enabled rule matching source and display locator."""
    ordered: Sequence[R] = sorted(rules, key=lambda rule: (rule.position, rule.id))
    return next(
        (
            rule
            for rule in ordered
            if rule.enabled
            and (rule.source is None or rule.source == source)
            and _glob_regex(rule.pattern).fullmatch(locator)
        ),
        None,
    )


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Compile the ownership glob grammar to a case-sensitive path expression."""
    parts: list[str] = ["^"]
    index: int = 0
    while index < len(pattern):
        char: str = pattern[index]
        if char == "*" and index + 1 < len(pattern) and pattern[index + 1] == "*":
            parts.append(".*")
            index += 2
        elif char == "*":
            parts.append("[^/]*")
            index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    parts.append("$")
    return re.compile("".join(parts))
