"""Issue stream period parsing and validation."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode

_PERIOD: re.Pattern[str] = re.compile(r"^([0-9]+)([hdw])$")
MAX_PERIOD: timedelta = timedelta(days=365)


@dataclass(frozen=True)
class Period:
    """Inclusive lower and exclusive upper time bounds."""

    start: datetime | None
    end: datetime | None


def parse_period(
    period: str | None, start: datetime | None, end: datetime | None, now: datetime
) -> Period:
    """Parse all, relative, or custom periods with a maximum one-year span."""
    current: datetime = ensure_utc(now)
    if period in (None, "all") and start is None and end is None:
        return Period(None, None)
    if start is not None or end is not None:
        if period not in (None, "custom") or start is None or end is None:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid period")
        lower: datetime = ensure_utc(start)
        upper: datetime = ensure_utc(end)
        if lower > upper or upper - lower > MAX_PERIOD:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid period")
        return Period(lower, upper)
    if period is None:
        return Period(None, None)
    match: re.Match[str] | None = _PERIOD.fullmatch(period)
    if match is None:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid period")
    amount: int = int(match.group(1))
    unit: str = match.group(2)
    width: timedelta = {
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
        "w": timedelta(weeks=amount),
    }[unit]
    if width > MAX_PERIOD:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid period")
    return Period(current - width, current)
