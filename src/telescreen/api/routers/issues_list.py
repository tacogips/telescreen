"""Issue stream and aggregate statistics API routes."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.pagination import PageParams, encode_cursor, page_params
from telescreen.api.routers.issues_common import (
    IssueItemExtras,
    IssuesRouterDeps,
    _bucket_start,
    _cursor,
    _issue_json,
    _optional_string,
    _response,
    _unavailable,
    issue_item_extras,
)
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_array, json_int, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode, IssueStatus, RuleClass, Severity, Visibility
from telescreen.db.models import FindingRow, IssueRow
from telescreen.issues.query.filters import QueryContext, build_filters
from telescreen.issues.query.model import (
    Comparison,
    ParsedQuery,
    QueryError,
    QueryKey,
    QueryTerm,
    TrendBucket,
)
from telescreen.issues.query.parser import parse_issue_query
from telescreen.issues.query.period import Period, parse_period
from telescreen.issues.query.trend import trends
from telescreen.ports.directory import AssigneeIds, RecipientResolver


def build_issues_list_router(deps: IssuesRouterDeps) -> APIRouter:
    """Build the issue stream routes."""
    router: APIRouter = APIRouter()

    @router.get("/api/issues")
    async def list_issues(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
        page: Annotated[PageParams, Depends(page_params)],
        status: IssueStatus | None = None,
        source: str | None = None,
        rule_class: RuleClass | None = None,
        severity: Severity | None = None,
        assignee: str | None = None,
        q: str | None = None,
        exposure: Visibility | None = None,
        query: str = "",
        period: str | None = "all",
        start: datetime | None = None,
        end: datetime | None = None,
        trend: Annotated[str, Query(pattern="^(24h|14d|none)$")] = "14d",
        sort: Annotated[
            str,
            Query(pattern="^(last_seen|first_seen|times_seen|values|risk|priority|trending)$"),
        ] = "last_seen",
    ) -> Response:
        """Return filtered stream items using a stable opaque keyset cursor."""
        now: datetime = ensure_utc(deps.ctx.clock.now())
        try:
            parsed: ParsedQuery = parse_issue_query(query)
            selected_period: Period = parse_period(period, start, end, now)
        except QueryError as error:
            return _invalid_query(error)
        if q:
            parsed = ParsedQuery(parsed.terms, parsed.text + (q,))
        resolver: RecipientResolver = deps.resolver or deps.service.resolver
        context: QueryContext = QueryContext(principal.member_id, resolver, deps.owners, now)
        try:
            async with deps.ctx.sessions() as session:
                predicates: list[ColumnElement[bool]] = list(
                    await build_filters(session, parsed, context)
                )
                if status is not None:
                    predicates.append(IssueRow.status == status.value)
                if source is not None:
                    predicates.append(IssueRow.source == source)
                if rule_class is not None:
                    predicates.append(IssueRow.rule_class == rule_class.value)
                if severity is not None:
                    predicates.append(IssueRow.severity == severity.value)
                if exposure is not None:
                    predicates.append(IssueRow.exposure_visibility == exposure.value)
                if assignee is not None:
                    if assignee in ("me", "my_teams") and deps.owners is None:
                        if principal.member_id is None:
                            raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
                        return _unavailable()
                    assignment_filter: ColumnElement[bool] = await _legacy_assignee(
                        session, assignee, context, resolver
                    )
                    predicates.append(assignment_filter)
                predicates.append(IssueRow.merged_into_id.is_(None))
                _period_filters(predicates, selected_period)
                statement: Select[tuple[IssueRow]] = select(IssueRow).where(*predicates)
                trend_expression: ScalarSelect[int] | None = (
                    _trend_expression(now) if sort == "trending" else None
                )
                priority_expression: ColumnElement[int] = case(
                    (IssueRow.priority == "high", 3),
                    (IssueRow.priority == "medium", 2),
                    (IssueRow.priority == "low", 1),
                    else_=0,
                )
                cursor_parts: tuple[str, ...] = _cursor(page.cursor)
                statement = _apply_cursor(
                    statement,
                    cursor_parts,
                    sort,
                    priority_expression,
                    trend_expression,
                )
                statement = _order(statement, sort, priority_expression, trend_expression)
                rows: list[IssueRow] = list(
                    (await session.scalars(statement.limit(page.limit + 1))).all()
                )
                has_more: bool = len(rows) > page.limit
                items: list[IssueRow] = rows[: page.limit]
                extras: tuple[IssueItemExtras, ...] = await issue_item_extras(
                    session, tuple(items), principal.member_id, deps.actors
                )
                if trend != "none" and items:
                    bucket: TrendBucket = (
                        TrendBucket.HOURLY_24 if trend == "24h" else TrendBucket.DAILY_14
                    )
                    trend_values: tuple[tuple[str, tuple[int, ...]], ...] = await trends(
                        session, tuple(item.id for item in items), bucket, now
                    )
                    extras = tuple(
                        replace(
                            extra,
                            trend=next(
                                values for issue_id, values in trend_values if issue_id == item.id
                            ),
                        )
                        for item, extra in zip(items, extras, strict=True)
                    )
                trend_cursor_count: int | None = None
                if has_more and items and sort == "trending" and trend_expression is not None:
                    trend_cursor_count = int(
                        await session.scalar(
                            select(trend_expression).where(IssueRow.id == items[-1].id)
                        )
                        or 0
                    )
        except QueryError as error:
            return _invalid_query(error)
        next_cursor: str | None = None
        if has_more and items:
            next_cursor = _next_cursor(items[-1], sort, trend_cursor_count)
        return _response(
            json_object(
                (
                    (
                        "items",
                        json_array(
                            tuple(
                                _issue_json(item, extra)
                                for item, extra in zip(items, extras, strict=True)
                            )
                        ),
                    ),
                    ("next_cursor", _optional_string(next_cursor)),
                )
            )
        )

    @router.get("/api/issues/stats")
    async def issue_stats(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
        bucket: Annotated[str, Query(pattern="^(day|hour)$")] = "day",
        days: Annotated[int, Query(ge=1, le=90)] = 14,
    ) -> Response:
        """Return zero-filled finding counts for the requested recent period."""
        del principal
        now: datetime = ensure_utc(deps.ctx.clock.now())
        width: timedelta = timedelta(days=1) if bucket == "day" else timedelta(hours=1)
        count: int = days if bucket == "day" else days * 24
        start: datetime = now.replace(minute=0, second=0, microsecond=0)
        if bucket == "day":
            start = start.replace(hour=0)
        start -= width * (count - 1)
        totals: list[int] = [0 for _ in range(count)]
        async with deps.ctx.sessions() as session:
            rows: Result[tuple[datetime]] = await session.execute(
                select(FindingRow.observed_at).where(FindingRow.observed_at >= start)
            )
            observed_values: list[datetime] = list(rows.scalars().all())
        for observed_at in observed_values:
            point: datetime = _bucket_start(ensure_utc(observed_at), bucket)
            offset: int = int((point - start).total_seconds() // width.total_seconds())
            if 0 <= offset < count:
                totals[offset] += 1
        bucket_values: list[JsonValue] = []
        for index in range(count):
            at: datetime = start + width * index
            bucket_values.append(
                json_object(
                    (("start", json_str(at.isoformat())), ("count", json_int(totals[index])))
                )
            )
        return _response(json_object((("buckets", json_array(tuple(bucket_values))),)))

    return router


async def _legacy_assignee(
    session: AsyncSession,
    assignee: str,
    context: QueryContext,
    resolver: RecipientResolver,
) -> ColumnElement[bool]:
    """Preserve legacy assignee syntax while fixing the unassigned sentinel."""
    if assignee == "none":
        return and_(IssueRow.assignee_member_id.is_(None), IssueRow.assignee_team_id.is_(None))
    if assignee in ("me", "my_teams"):
        term: QueryTerm = QueryTerm(QueryKey.ASSIGNED, (assignee,), False, Comparison.EQ)
        return (await build_filters(session, ParsedQuery((term,), ()), context))[0]
    resolved: AssigneeIds = await resolver.assignee_of(session, assignee)
    if resolved.member_id is not None:
        return IssueRow.assignee_member_id == resolved.member_id
    return IssueRow.assignee_team_id == resolved.team_id


def _period_filters(filters: list[ColumnElement[bool]], period: Period) -> None:
    """Apply issue-time overlap bounds to a stream query."""
    if period.start is not None:
        filters.append(IssueRow.last_seen >= period.start)
    if period.end is not None:
        filters.append(IssueRow.first_seen <= period.end)


def _trend_expression(now: datetime) -> ScalarSelect[int]:
    """Build the correlated 24-hour group finding count used for trending sort."""
    group_issue: type[IssueRow] = aliased(IssueRow)
    return (
        select(func.count(FindingRow.id))
        .select_from(FindingRow)
        .join(group_issue, FindingRow.issue_id == group_issue.id)
        .where(
            or_(group_issue.id == IssueRow.id, group_issue.merged_into_id == IssueRow.id),
            FindingRow.observed_at >= now - timedelta(hours=24),
        )
        .correlate(IssueRow)
        .scalar_subquery()
    )


def _apply_cursor(
    statement: Select[tuple[IssueRow]],
    cursor: tuple[str, ...],
    sort: str,
    priority_expression: ColumnElement[int],
    trend_expression: ScalarSelect[int] | None,
) -> Select[tuple[IssueRow]]:
    """Add the strict after-cursor predicate for the selected descending tuple."""
    if not cursor:
        return statement
    expected: int = 3 if sort in ("priority", "trending", "values") else 2
    if len(cursor) != expected or not cursor[-1]:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
    identifier: str = cursor[-1]
    if sort in ("priority", "trending", "values"):
        try:
            first: int = int(cursor[0])
            marker_at: datetime = ensure_utc(datetime.fromisoformat(cursor[1]))
        except ValueError:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
        if sort == "priority":
            primary: ColumnElement[int] = priority_expression
        elif sort == "trending" and trend_expression is not None:
            primary = trend_expression
        else:
            primary = IssueRow.distinct_fingerprints
        return statement.where(
            or_(
                primary < first,
                and_(primary == first, IssueRow.last_seen < marker_at),
                and_(primary == first, IssueRow.last_seen == marker_at, IssueRow.id < identifier),
            )
        )
    if sort in ("times_seen", "risk"):
        try:
            marker_count: int = int(cursor[0])
        except ValueError:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
        if sort == "times_seen":
            column: InstrumentedAttribute[int] = IssueRow.times_seen
        else:
            column = IssueRow.risk_score
        return statement.where(
            or_(column < marker_count, and_(column == marker_count, IssueRow.id < identifier))
        )
    try:
        marker_time: datetime = ensure_utc(datetime.fromisoformat(cursor[0]))
    except ValueError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
    column_time: InstrumentedAttribute[datetime] = (
        IssueRow.first_seen if sort == "first_seen" else IssueRow.last_seen
    )
    return statement.where(
        or_(column_time < marker_time, and_(column_time == marker_time, IssueRow.id < identifier))
    )


def _order(
    statement: Select[tuple[IssueRow]],
    sort: str,
    priority_expression: ColumnElement[int],
    trend_expression: ScalarSelect[int] | None,
) -> Select[tuple[IssueRow]]:
    """Set the deterministic descending sort tuple."""
    if sort == "priority":
        return statement.order_by(
            priority_expression.desc(), IssueRow.last_seen.desc(), IssueRow.id.desc()
        )
    if sort == "trending" and trend_expression is not None:
        return statement.order_by(
            trend_expression.desc(), IssueRow.last_seen.desc(), IssueRow.id.desc()
        )
    if sort == "values":
        return statement.order_by(
            IssueRow.distinct_fingerprints.desc(), IssueRow.last_seen.desc(), IssueRow.id.desc()
        )
    if sort == "times_seen":
        return statement.order_by(IssueRow.times_seen.desc(), IssueRow.id.desc())
    if sort == "risk":
        return statement.order_by(IssueRow.risk_score.desc(), IssueRow.id.desc())
    if sort == "first_seen":
        return statement.order_by(IssueRow.first_seen.desc(), IssueRow.id.desc())
    return statement.order_by(IssueRow.last_seen.desc(), IssueRow.id.desc())


def _next_cursor(
    issue: IssueRow,
    sort: str,
    trend_count: int | None,
) -> str:
    """Encode the selected row's complete ordering tuple."""
    if sort == "priority":
        rank: int = _priority_rank(issue.priority)
        return encode_cursor((str(rank), ensure_utc(issue.last_seen).isoformat(), issue.id))
    if sort == "values":
        return encode_cursor(
            (str(issue.distinct_fingerprints), ensure_utc(issue.last_seen).isoformat(), issue.id)
        )
    if sort == "trending":
        return encode_cursor(
            (str(trend_count or 0), ensure_utc(issue.last_seen).isoformat(), issue.id)
        )
    if sort == "times_seen":
        return encode_cursor((str(issue.times_seen), issue.id))
    if sort == "risk":
        return encode_cursor((str(issue.risk_score), issue.id))
    column_time: datetime = issue.first_seen if sort == "first_seen" else issue.last_seen
    return encode_cursor((ensure_utc(column_time).isoformat(), issue.id))


def _priority_rank(value: str) -> int:
    """Return the descending priority rank used in stable cursors."""
    if value == "high":
        return 3
    if value == "medium":
        return 2
    if value == "low":
        return 1
    return 0


def _invalid_query(error: QueryError) -> Response:
    """Render a safe parser error without reflecting user query content."""
    return _response(
        json_object(
            (
                (
                    "error",
                    json_object(
                        (
                            ("code", json_str(ErrorCode.INVALID_QUERY.value)),
                            ("detail", json_str("invalid query")),
                            ("token_index", json_int(error.token_index)),
                            ("reason", json_str(error.reason.value)),
                        )
                    ),
                ),
            )
        ),
        status_code=422,
    )
