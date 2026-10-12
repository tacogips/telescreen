"""Issue list API routes."""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.engine import Result
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import Select

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.pagination import PageParams, encode_cursor, page_params
from telescreen.api.routers.issues_common import (
    IssuesRouterDeps,
    _bucket_start,
    _cursor,
    _issue_json,
    _optional_string,
    _response,
    _unavailable,
)
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_array, json_int, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode, IssueStatus, RuleClass, Severity, Visibility
from telescreen.db.models import FindingRow, IssueRow
from telescreen.ports.directory import AssigneeIds, RecipientResolver


def build_issues_list_router(deps: IssuesRouterDeps) -> APIRouter:
    """Build the list issue API routes."""
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
        sort: Annotated[
            str, Query(pattern="^(last_seen|first_seen|times_seen|risk)$")
        ] = "last_seen",
    ) -> Response:
        """Return filtered issues with an opaque keyset cursor."""
        async with deps.ctx.sessions() as session:
            query: Select[tuple[IssueRow]] = select(IssueRow)
            if status is not None:
                query = query.where(IssueRow.status == status.value)
            if source is not None:
                query = query.where(IssueRow.source == source)
            if rule_class is not None:
                query = query.where(IssueRow.rule_class == rule_class.value)
            if severity is not None:
                query = query.where(IssueRow.severity == severity.value)
            if exposure is not None:
                query = query.where(IssueRow.exposure_visibility == exposure.value)
            if assignee is not None:
                if assignee in ("me", "my_teams"):
                    if principal.member_id is None:
                        raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
                    if deps.owners is None:
                        return _unavailable()
                    resolver: RecipientResolver = deps.resolver or deps.service.resolver
                    if assignee == "me":
                        member_ref: str | None = await deps.owners.member_ref(
                            session, principal.member_id
                        )
                        if member_ref is None:
                            raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
                        assignment: AssigneeIds = await resolver.assignee_of(session, member_ref)
                        query = query.where(IssueRow.assignee_member_id == assignment.member_id)
                    else:
                        team_refs: tuple[str, ...] = await deps.owners.team_refs_of(
                            session, principal.member_id
                        )
                        team_ids: list[str] = []
                        for team_ref in team_refs:
                            team_assignment: AssigneeIds = await resolver.assignee_of(
                                session, team_ref
                            )
                            if team_assignment.team_id is not None:
                                team_ids.append(team_assignment.team_id)
                        query = (
                            query.where(IssueRow.assignee_team_id.in_(team_ids))
                            if team_ids
                            else query.where(IssueRow.id == "")
                        )
                else:
                    resolver = deps.resolver or deps.service.resolver
                    assignment = await resolver.assignee_of(session, assignee)
                    if assignment.member_id is not None:
                        query = query.where(IssueRow.assignee_member_id == assignment.member_id)
                    else:
                        query = query.where(IssueRow.assignee_team_id == assignment.team_id)
            if q is not None:
                query = query.where(
                    IssueRow.display_title.like(f"%{q}%") | IssueRow.display_locator.like(f"%{q}%")
                )
            cursor_parts: tuple[str, ...] = _cursor(page.cursor)
            if cursor_parts:
                if len(cursor_parts) != 2:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
                marker: str = cursor_parts[0]
                identifier: str = cursor_parts[1]
                if sort == "times_seen":
                    try:
                        marker_count: int = int(marker)
                    except ValueError:
                        raise TelescreenError(
                            ErrorCode.VALIDATION_ERROR, "invalid cursor"
                        ) from None
                    query = query.where(
                        (IssueRow.times_seen < marker_count)
                        | ((IssueRow.times_seen == marker_count) & (IssueRow.id < identifier))
                    )
                    query = query.order_by(IssueRow.times_seen.desc(), IssueRow.id.desc())
                elif sort == "risk":
                    try:
                        marker_risk: int = int(marker)
                    except ValueError:
                        raise TelescreenError(
                            ErrorCode.VALIDATION_ERROR, "invalid cursor"
                        ) from None
                    query = query.where(
                        (IssueRow.risk_score < marker_risk)
                        | ((IssueRow.risk_score == marker_risk) & (IssueRow.id < identifier))
                    )
                    query = query.order_by(IssueRow.risk_score.desc(), IssueRow.id.desc())
                else:
                    try:
                        marker_at: datetime = ensure_utc(datetime.fromisoformat(marker))
                    except ValueError:
                        raise TelescreenError(
                            ErrorCode.VALIDATION_ERROR, "invalid cursor"
                        ) from None
                    column: InstrumentedAttribute[datetime] = (
                        IssueRow.last_seen if sort == "last_seen" else IssueRow.first_seen
                    )
                    query = query.where(
                        (column < marker_at) | ((column == marker_at) & (IssueRow.id < identifier))
                    )
                    query = query.order_by(column.desc(), IssueRow.id.desc())
            elif sort == "times_seen":
                query = query.order_by(IssueRow.times_seen.desc(), IssueRow.id.desc())
            elif sort == "risk":
                query = query.order_by(IssueRow.risk_score.desc(), IssueRow.id.desc())
            elif sort == "first_seen":
                query = query.order_by(IssueRow.first_seen.desc(), IssueRow.id.desc())
            else:
                query = query.order_by(IssueRow.last_seen.desc(), IssueRow.id.desc())
            rows: list[IssueRow] = list((await session.scalars(query.limit(page.limit + 1))).all())
        has_more: bool = len(rows) > page.limit
        items: list[IssueRow] = rows[: page.limit]
        next_cursor: str | None = None
        if has_more and items:
            last: IssueRow = items[-1]
            marker_value: str = str(last.times_seen) if sort == "times_seen" else ""
            if sort == "last_seen":
                marker_value = ensure_utc(last.last_seen).isoformat()
            elif sort == "first_seen":
                marker_value = ensure_utc(last.first_seen).isoformat()
            elif sort == "risk":
                marker_value = str(last.risk_score)
            next_cursor = encode_cursor((marker_value, last.id))
        return _response(
            json_object(
                (
                    ("items", json_array(tuple(_issue_json(row) for row in items))),
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
