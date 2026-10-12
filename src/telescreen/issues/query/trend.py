"""SQLite-compatible per-issue finding trend aggregation."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.db.models import FindingRow, IssueRow
from telescreen.issues.query.model import TrendBucket


async def trends(
    session: AsyncSession,
    issue_ids: tuple[str, ...],
    bucket: TrendBucket,
    now: datetime,
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """Return zero-filled counts for each issue and its merged children."""
    width: timedelta = timedelta(hours=1) if bucket is TrendBucket.HOURLY_24 else timedelta(days=1)
    count: int = 24 if bucket is TrendBucket.HOURLY_24 else 14
    current: datetime = ensure_utc(now)
    latest: datetime = current.replace(minute=0, second=0, microsecond=0)
    if bucket is TrendBucket.DAILY_14:
        latest = latest.replace(hour=0)
    start: datetime = latest - width * (count - 1)
    raw_rows: Result[tuple[str, str | None]] = await session.execute(
        select(IssueRow.id, IssueRow.merged_into_id).where(
            (IssueRow.id.in_(issue_ids)) | (IssueRow.merged_into_id.in_(issue_ids))
        )
    )
    rows: list[tuple[str, str | None]] = [(row[0], row[1]) for row in raw_rows.all()]
    parent_for: list[tuple[str, str]] = [(issue_id, issue_id) for issue_id in issue_ids]
    for issue_id, parent_id in rows:
        if parent_id in issue_ids:
            parent_for.append((issue_id, parent_id))
    buckets: list[list[int]] = [[0] * count for _ in issue_ids]
    if not parent_for:
        return tuple(
            (issue_id, tuple(values)) for issue_id, values in zip(issue_ids, buckets, strict=True)
        )
    raw_observed: Result[tuple[str, datetime]] = await session.execute(
        select(FindingRow.issue_id, FindingRow.observed_at).where(
            FindingRow.issue_id.in_(tuple(issue_id for issue_id, _ in parent_for)),
            FindingRow.observed_at >= start,
        )
    )
    observed: list[tuple[str, datetime]] = [(row[0], row[1]) for row in raw_observed.all()]
    for finding_issue_id, observed_at in observed:
        point: datetime = _bucket_start(ensure_utc(observed_at), bucket)
        offset: int = int((point - start).total_seconds() // width.total_seconds())
        if 0 <= offset < count:
            parent_id: str = next(
                parent for issue_id, parent in parent_for if issue_id == finding_issue_id
            )
            buckets[issue_ids.index(parent_id)][offset] += 1
    return tuple(
        (issue_id, tuple(values)) for issue_id, values in zip(issue_ids, buckets, strict=True)
    )


def _bucket_start(value: datetime, bucket: TrendBucket) -> datetime:
    """Normalize a timestamp to the start of its hour or UTC day."""
    normalized: datetime = ensure_utc(value)
    if bucket is TrendBucket.HOURLY_24:
        return normalized.replace(minute=0, second=0, microsecond=0)
    return normalized.replace(hour=0, minute=0, second=0, microsecond=0)
