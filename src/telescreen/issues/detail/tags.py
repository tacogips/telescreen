"""Finding tag distributions for issue detail."""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select

from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import FindingRow, IssueRow

_LOCATION_SEPARATOR: re.Pattern[str] = re.compile(r"[ >/:]")


@dataclass(frozen=True)
class TagValue:
    """One counted tag value."""

    value: str
    count: int
    percent: float


@dataclass(frozen=True)
class Tag:
    """One tag key and its top values."""

    key: str
    values: tuple[TagValue, ...]
    other_count: int


@dataclass(frozen=True)
class TagDistribution:
    """Finding total and safe tag projections."""

    total: int
    tags: tuple[Tag, ...]


async def tag_distribution(
    session: AsyncSession,
    issue_ids: tuple[str, ...],
    period: tuple[datetime | None, datetime | None],
    grouped: bool,
) -> TagDistribution:
    """Count tags over all findings, including merged children (B7)."""
    start: datetime | None = period[0]
    end: datetime | None = period[1]
    filters: list[ColumnElement[bool]] = []
    if issue_ids:
        filters.append(FindingRow.issue_id.in_(issue_ids))
    else:
        raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
    if start is not None:
        filters.append(FindingRow.observed_at >= ensure_utc(start))
    if end is not None:
        filters.append(FindingRow.observed_at < ensure_utc(end))
    total_query: Select[tuple[int]] = select(func.count()).select_from(FindingRow).where(*filters)
    total: int = int(await session.scalar(total_query) or 0)
    columns: tuple[tuple[str, InstrumentedAttribute[str]], ...] = (
        ("detector", FindingRow.detector),
        ("rule_id", FindingRow.rule_id),
        ("location", FindingRow.location),
    )
    if grouped:
        columns = (*columns, ("source", FindingRow.source), ("member", FindingRow.issue_id))
    tags: list[Tag] = []
    for key, column in columns:
        query: Select[tuple[str, int]] = (
            select(column, func.count())
            .where(*filters)
            .group_by(column)
            .order_by(func.count().desc(), column)
        )
        result: Result[tuple[str, int]] = await session.execute(query)
        counts: list[tuple[str, int]] = []
        for value, count in result.all():
            normalized: str = str(value or "")
            if key == "location":
                normalized = _LOCATION_SEPARATOR.split(normalized, maxsplit=1)[0]
            elif key == "member":
                issue: IssueRow | None = await session.get(IssueRow, normalized)
                normalized = f"TELE-{issue.number}" if issue is not None else "Unknown"
            counts.append((normalized, int(count)))
        merged_counts: list[tuple[str, int]] = []
        for value, count in counts:
            existing_index: int | None = next(
                (index for index, item in enumerate(merged_counts) if item[0] == value), None
            )
            if existing_index is None:
                merged_counts.append((value, count))
            else:
                merged_counts[existing_index] = (value, merged_counts[existing_index][1] + count)
        sorted_counts: list[tuple[str, int]] = sorted(
            merged_counts, key=lambda item: (-item[1], item[0])
        )
        top: tuple[tuple[str, int], ...] = tuple(sorted_counts[:10])
        other_count: int = sum(count for _, count in sorted_counts[10:])
        values: tuple[TagValue, ...] = tuple(
            TagValue(value, count, round(count * 100 / total, 1) if total else 0.0)
            for value, count in top
        )
        tags.append(Tag(key, values, other_count))
    return TagDistribution(total, tuple(tags))
