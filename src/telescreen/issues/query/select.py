"""Server-side bounded selection for issue bulk operations."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from telescreen.db.models import IssueRow
from telescreen.issues.query.filters import QueryContext, build_filters
from telescreen.issues.query.model import ParsedQuery
from telescreen.issues.query.parser import parse_issue_query
from telescreen.issues.query.period import Period


@dataclass(frozen=True)
class MatchResult:
    """Capped matching issue identifiers and the complete match count."""

    ids: tuple[str, ...]
    matched: int


async def select_matching_ids(
    session: AsyncSession,
    query_text: str,
    period: Period,
    ctx: QueryContext,
    limit: int,
) -> MatchResult:
    """Select up to limit matching issue IDs while reporting the exact count."""
    parsed: ParsedQuery = parse_issue_query(query_text)
    filters: list[ColumnElement[bool]] = list(await build_filters(session, parsed, ctx))
    filters.append(IssueRow.merged_into_id.is_(None))
    if period.start is not None:
        filters.append(IssueRow.last_seen >= period.start)
    if period.end is not None:
        filters.append(IssueRow.first_seen <= period.end)
    matched: int = int(
        await session.scalar(select(func.count()).select_from(IssueRow).where(*filters)) or 0
    )
    identifiers: list[str] = list(
        (
            await session.scalars(
                select(IssueRow.id)
                .where(*filters)
                .order_by(IssueRow.last_seen.desc(), IssueRow.id.desc())
                .limit(max(0, limit))
            )
        ).all()
    )
    return MatchResult(tuple(identifiers), matched)
