"""Compile parsed issue query expressions into SQLAlchemy predicates."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    UNRESOLVED_STATUSES,
    ErrorCode,
)
from telescreen.db.models import (
    ExternalIssueLinkRow,
    IssueBookmarkRow,
    IssueExceptionRow,
    IssueRow,
)
from telescreen.issues.query.model import Comparison, ParsedQuery, QueryKey, QueryTerm
from telescreen.ports.directory import AssigneeIds, OwnerDirectory, RecipientResolver


@dataclass(frozen=True)
class QueryContext:
    """Identity and time dependencies used while compiling issue filters."""

    member_id: str | None
    resolver: RecipientResolver
    owners: OwnerDirectory | None
    now: datetime


async def build_filters(
    session: AsyncSession, parsed: ParsedQuery, ctx: QueryContext
) -> tuple[ColumnElement[bool], ...]:
    """Compile query terms and free text into AND-combined SQL predicates."""
    filters: list[ColumnElement[bool]] = []
    for term in parsed.terms:
        predicate: ColumnElement[bool] = await _term_filter(session, term, ctx)
        filters.append(not_(predicate) if term.negated else predicate)
    for text in parsed.text:
        if text.upper().startswith("TELE-") and text[5:].isdecimal():
            filters.append(IssueRow.number == int(text[5:]))
        else:
            escaped: str = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern: str = f"%{escaped}%"
            filters.append(
                IssueRow.display_title.ilike(pattern, escape="\\")
                | IssueRow.display_locator.ilike(pattern, escape="\\")
            )
    return tuple(filters)


async def _term_filter(
    session: AsyncSession, term: QueryTerm, ctx: QueryContext
) -> ColumnElement[bool]:
    """Compile one term without logging or returning its source text."""
    key: QueryKey = term.key
    values: tuple[str, ...] = term.values
    if key is QueryKey.IS:
        if values[0] == "unresolved":
            return IssueRow.status.in_(tuple(status.value for status in UNRESOLVED_STATUSES))
        if values[0] == "for_review":
            return and_(
                IssueRow.status.in_(tuple(status.value for status in UNRESOLVED_STATUSES)),
                IssueRow.needs_review.is_(True),
            )
        if values[0] == "assigned":
            return or_(
                IssueRow.assignee_member_id.is_not(None), IssueRow.assignee_team_id.is_not(None)
            )
        if values[0] == "unassigned":
            return and_(IssueRow.assignee_member_id.is_(None), IssueRow.assignee_team_id.is_(None))
        if values[0] == "public":
            return IssueRow.exposure_visibility == "public"
        return IssueRow.status == values[0]
    if key is QueryKey.STATUS:
        return _one_of(IssueRow.status, values)
    if key is QueryKey.SEVERITY:
        return _one_of(IssueRow.severity, values)
    if key is QueryKey.PRIORITY:
        return _one_of(IssueRow.priority, values)
    if key is QueryKey.RULE:
        return _one_of(IssueRow.rule_class, values)
    if key is QueryKey.SOURCE:
        return _one_of(IssueRow.source, values)
    if key is QueryKey.EXPOSURE:
        return _one_of(IssueRow.exposure_visibility, values)
    if key is QueryKey.ASSIGNED:
        return await _assigned(session, values[0], ctx)
    if key is QueryKey.HAS:
        return _has(values[0])
    if key is QueryKey.BOOKMARKS:
        if ctx.member_id is None:
            raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
        return exists(
            select(IssueBookmarkRow.issue_id).where(
                IssueBookmarkRow.issue_id == IssueRow.id,
                IssueBookmarkRow.member_id == ctx.member_id,
            )
        )
    if key in (QueryKey.TIMES_SEEN, QueryKey.VALUES):
        column: InstrumentedAttribute[int] = (
            IssueRow.times_seen if key is QueryKey.TIMES_SEEN else IssueRow.distinct_fingerprints
        )
        return _compare_int(column, int(values[0]), term.comparison)
    if key in (QueryKey.FIRST_SEEN, QueryKey.LAST_SEEN):
        column: InstrumentedAttribute[datetime] = (
            IssueRow.first_seen if key is QueryKey.FIRST_SEEN else IssueRow.last_seen
        )
        value: str = values[0]
        if len(value) == 10 and term.comparison is Comparison.EQ:
            boundary: datetime = datetime.combine(
                date.fromisoformat(value), datetime.min.time(), UTC
            )
            return and_(column >= boundary, column < boundary + timedelta(days=1))
        if value.startswith("-") and term.comparison is Comparison.EQ:
            return column >= _date_value(value, ctx.now)
        if value.startswith("+") and term.comparison is Comparison.EQ:
            return column < _date_value("-" + value[1:], ctx.now)
        boundary = _date_value(value, ctx.now)
        return _compare_datetime(column, boundary, term.comparison)
    return IssueRow.id == ""


async def _assigned(session: AsyncSession, value: str, ctx: QueryContext) -> ColumnElement[bool]:
    """Resolve an assignment filter, preserving unassigned as a resolver-free case."""
    if value == "none":
        return and_(IssueRow.assignee_member_id.is_(None), IssueRow.assignee_team_id.is_(None))
    if value in ("me", "my_teams") and ctx.member_id is None:
        raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
    if value == "my_teams":
        if ctx.owners is None:
            return IssueRow.id == ""
        refs: tuple[str, ...] = await ctx.owners.team_refs_of(session, ctx.member_id or "")
        assignments: list[str] = []
        for ref in refs:
            resolved: AssigneeIds = await ctx.resolver.assignee_of(session, ref)
            if resolved.team_id is not None:
                assignments.append(resolved.team_id)
        return IssueRow.assignee_team_id.in_(assignments) if assignments else IssueRow.id == ""
    if value == "me":
        if ctx.owners is None:
            raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
        member_ref: str | None = await ctx.owners.member_ref(session, ctx.member_id or "")
        if member_ref is None:
            raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
        assigned: AssigneeIds = await ctx.resolver.assignee_of(session, member_ref)
        return IssueRow.assignee_member_id == assigned.member_id
    ref: str = f"team:{value[1:]}" if value.startswith("#") else value
    assigned = await ctx.resolver.assignee_of(session, ref)
    if assigned.member_id is not None:
        return IssueRow.assignee_member_id == assigned.member_id
    return IssueRow.assignee_team_id == assigned.team_id


def _has(value: str) -> ColumnElement[bool]:
    """Build a supported relationship existence predicate."""
    if value == "assignee":
        return or_(IssueRow.assignee_member_id.is_not(None), IssueRow.assignee_team_id.is_not(None))
    if value == "owner":
        return IssueRow.owner_ref.is_not(None)
    if value == "exception":
        return exists(
            select(IssueExceptionRow.id).where(
                IssueExceptionRow.issue_id == IssueRow.id,
                IssueExceptionRow.status.in_(("requested", "active")),
            )
        )
    return exists(
        select(ExternalIssueLinkRow.id).where(ExternalIssueLinkRow.issue_id == IssueRow.id)
    )


def _one_of(column: InstrumentedAttribute[str], values: tuple[str, ...]) -> ColumnElement[bool]:
    """Match any value in a parsed list."""
    return column.in_(values)


def _compare_int(
    column: InstrumentedAttribute[int], value: int, comparison: Comparison
) -> ColumnElement[bool]:
    """Apply a numeric comparison operator to an ORM column."""
    if comparison is Comparison.GT:
        return column > value
    if comparison is Comparison.GTE:
        return column >= value
    if comparison is Comparison.LT:
        return column < value
    if comparison is Comparison.LTE:
        return column <= value
    return column == value


def _compare_datetime(
    column: InstrumentedAttribute[datetime], value: datetime, comparison: Comparison
) -> ColumnElement[bool]:
    """Apply a timestamp comparison operator to an ORM column."""
    if comparison is Comparison.GT:
        return column > value
    if comparison is Comparison.GTE:
        return column >= value
    if comparison is Comparison.LT:
        return column < value
    if comparison is Comparison.LTE:
        return column <= value
    return column == value


def _date_value(value: str, now: datetime) -> datetime:
    """Convert a validated absolute or relative date value to UTC."""
    current: datetime = ensure_utc(now)
    if value[0] in ("+", "-"):
        amount: int = int(value[1:-1])
        unit: str = value[-1]
        delta: timedelta = {
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }[unit]
        return current + delta if value[0] == "+" else current - delta
    try:
        return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        parsed_date: datetime = datetime.fromisoformat(f"{value}T00:00:00+00:00")
        return parsed_date.astimezone(UTC)
