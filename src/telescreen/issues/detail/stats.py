"""Zero-filled finding count buckets for issue detail."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import FindingRow


@dataclass(frozen=True)
class StatBucket:
    """One timestamped finding count bucket."""

    at: datetime
    count: int


async def issue_stats(
    session: AsyncSession,
    issue_ids: tuple[str, ...],
    period: tuple[datetime, datetime],
    bucket: str,
    now: datetime,
) -> tuple[StatBucket, ...]:
    """Return zero-filled hourly or daily counts across a merged issue group (B7)."""
    if not issue_ids:
        raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
    width: timedelta = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
    if bucket not in ("hour", "day"):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid bucket")
    start: datetime = ensure_utc(period[0])
    end: datetime = ensure_utc(period[1])
    current: datetime = _floor(start, bucket)
    counts: list[tuple[datetime, int]] = []
    rows: Result[tuple[datetime, int]] = await session.execute(
        select(FindingRow.observed_at, func.count())
        .where(
            FindingRow.issue_id.in_(issue_ids),
            FindingRow.observed_at >= start,
            FindingRow.observed_at < end,
        )
        .group_by(FindingRow.observed_at)
    )
    for observed_at, count in rows.all():
        key: datetime = ensure_utc(observed_at)
        key = _floor(key, bucket)
        existing_index: int | None = next(
            (index for index, item in enumerate(counts) if item[0] == key), None
        )
        if existing_index is None:
            counts.append((key, int(count)))
        else:
            counts[existing_index] = (key, counts[existing_index][1] + int(count))
    del now
    if end < start:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid period")
    result: list[StatBucket] = []
    while current < end:
        count: int = next((value for at, value in counts if at == current), 0)
        result.append(StatBucket(current, count))
        current += width
    return tuple(result)


def _floor(value: datetime, bucket: str) -> datetime:
    """Floor a timestamp to its UTC hour or day."""
    if bucket == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)
