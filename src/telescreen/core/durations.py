"""Parse the supported ISO-8601 duration subset."""

import re
from datetime import timedelta

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode

_DURATION_PATTERN: re.Pattern[str] = re.compile(
    r"^P(?:(?P<weeks>[0-9]+)W|(?P<days>[0-9]+)D)?"
    r"(?:T(?:(?P<hours>[0-9]+)H)?(?:(?P<minutes>[0-9]+)M)?"
    r"(?:(?P<seconds>[0-9]+(?:\.[0-9]+)?)S)?)?$"
)


def parse_duration(text: str) -> timedelta:
    """Parse weeks, days and time components from an ISO-8601 duration."""
    match: re.Match[str] | None = _DURATION_PATTERN.fullmatch(text)
    if match is None or text in ("P", "PT") or text.endswith("T"):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid duration")
    try:
        return timedelta(
            weeks=int(match.group("weeks") or 0),
            days=int(match.group("days") or 0),
            hours=int(match.group("hours") or 0),
            minutes=int(match.group("minutes") or 0),
            seconds=float(match.group("seconds") or 0.0),
        )
    except (OverflowError, ValueError):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid duration") from None
