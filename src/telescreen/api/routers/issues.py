"""Issue list, lifecycle, history, and finding endpoints."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.engine import Result
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import Select

from telescreen.api.common.auth import Principal, require_scope
from telescreen.api.common.context import ApiContext
from telescreen.api.common.pagination import PageParams, decode_cursor, encode_cursor, page_params
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_float,
    json_int,
    json_null,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.vocabulary import (
    ApiScope,
    ErrorCode,
    ExceptionDuration,
    ExceptionStatus,
    IssueResolution,
    IssueStatus,
    RemediationAction,
    RuleClass,
    Severity,
    Visibility,
)
from telescreen.db.models import FindingRow, IssueExceptionRow, IssueHistoryRow, IssueRow
from telescreen.issues.exceptions import ExceptionService
from telescreen.issues.lifecycle import IssueRemediationService
from telescreen.issues.occurrences import OccurrenceResult, linked
from telescreen.issues.service import IssueService
from telescreen.ports.directory import AssigneeIds, OwnerDirectory, RecipientResolver
from telescreen.ports.issues import RemediationReceipt
from telescreen.ports.search import SearchIndexer


@pydantic.dataclasses.dataclass
class IssuePatchBody:
    """Validated issue status and assignment patch."""

    status: IssueStatus | None = None
    assignee_ref: str | None = None


@pydantic.dataclasses.dataclass
class CommentBody:
    """Validated user comment body."""

    body: str


@pydantic.dataclasses.dataclass(frozen=True)
class RemediationBody:
    """Validated read-only remediation request."""

    action: RemediationAction
    include_linked: bool = False
    note: str | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class ResolveBody:
    """Validated reasoned issue resolution."""

    reason: IssueResolution
    note: str


@pydantic.dataclasses.dataclass(frozen=True)
class ExceptionRequestBody:
    """Validated exception request."""

    reason: str
    duration: ExceptionDuration


@dataclass(frozen=True)
class IssuesRouterDeps:
    """Dependencies needed by issue routes."""

    ctx: ApiContext
    service: IssueService
    indexer: SearchIndexer
    remediation: IssueRemediationService | None = None
    exceptions: ExceptionService | None = None
    owners: OwnerDirectory | None = None
    resolver: RecipientResolver | None = None


def build_issues_router(deps: IssuesRouterDeps) -> APIRouter:
    """Build the authenticated issue REST API."""
    router: APIRouter = APIRouter()

    @router.get("/api/issues")
    async def list_issues(
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
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

    @router.get("/api/issues/{issue_id}/events")
    async def issue_events(
        issue_id: str,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
        page: Annotated[PageParams, Depends(page_params)],
    ) -> Response:
        """Return masked finding observations for an issue."""
        del principal
        after: tuple[str, ...] = _cursor(page.cursor)
        async with deps.ctx.sessions() as session:
            if await deps.service.repository.by_id(session, issue_id) is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
            query: Select[tuple[FindingRow]] = select(FindingRow).where(
                FindingRow.issue_id == issue_id
            )
            if after:
                if len(after) != 2:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
                try:
                    marker_at: datetime = ensure_utc(datetime.fromisoformat(after[0]))
                except ValueError:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
                query = query.where(
                    (FindingRow.observed_at < marker_at)
                    | ((FindingRow.observed_at == marker_at) & (FindingRow.id < after[1]))
                )
            rows: list[FindingRow] = list(
                (
                    await session.scalars(
                        query.order_by(FindingRow.observed_at.desc(), FindingRow.id.desc()).limit(
                            page.limit + 1
                        )
                    )
                ).all()
            )
        has_more: bool = len(rows) > page.limit
        items: list[FindingRow] = rows[: page.limit]
        next_cursor: str | None = None
        if has_more and items:
            last: FindingRow = items[-1]
            next_cursor = encode_cursor((ensure_utc(last.observed_at).isoformat(), last.id))
        return _response(
            json_object(
                (
                    ("items", json_array(tuple(_finding_json(row) for row in items))),
                    ("next_cursor", _optional_string(next_cursor)),
                )
            )
        )

    @router.get("/api/issues/{issue_id}/history")
    async def issue_history(
        issue_id: str,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
        page: Annotated[PageParams, Depends(page_params)],
    ) -> Response:
        """Return chronological masked issue history."""
        del principal
        after: tuple[str, ...] = _cursor(page.cursor)
        async with deps.ctx.sessions() as session:
            if await deps.service.repository.by_id(session, issue_id) is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
            query: Select[tuple[IssueHistoryRow]] = select(IssueHistoryRow).where(
                IssueHistoryRow.issue_id == issue_id
            )
            if after:
                if len(after) != 2:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
                try:
                    marker_at: datetime = ensure_utc(datetime.fromisoformat(after[0]))
                except ValueError:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
                query = query.where(
                    (IssueHistoryRow.at > marker_at)
                    | ((IssueHistoryRow.at == marker_at) & (IssueHistoryRow.id > after[1]))
                )
            rows: list[IssueHistoryRow] = list(
                (
                    await session.scalars(
                        query.order_by(IssueHistoryRow.at, IssueHistoryRow.id).limit(page.limit + 1)
                    )
                ).all()
            )
        has_more: bool = len(rows) > page.limit
        items: list[IssueHistoryRow] = rows[: page.limit]
        next_cursor: str | None = None
        if has_more and items:
            last: IssueHistoryRow = items[-1]
            next_cursor = encode_cursor((ensure_utc(last.at).isoformat(), last.id))
        return _response(
            json_object(
                (
                    ("items", json_array(tuple(_history_json(row) for row in items))),
                    ("next_cursor", _optional_string(next_cursor)),
                )
            )
        )

    @router.get("/api/issues/stats")
    async def issue_stats(
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
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

    @router.get("/api/issues/{issue_id}")
    async def get_issue(
        issue_id: str,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
    ) -> Response:
        """Return a masked issue record."""
        async with deps.ctx.sessions() as session:
            issue: IssueRow | None = await deps.service.repository.by_id(session, issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
            assignee_ref: str | None = None
            owner_member_ref: str | None = None
            if deps.owners is not None:
                owner_member_ref = (
                    await deps.owners.member_for_owner(session, issue.owner_ref)
                    if issue.owner_ref is not None
                    else None
                )
                if issue.assignee_member_id is not None:
                    assignee_ref = await deps.owners.member_ref(session, issue.assignee_member_id)
                elif issue.assignee_team_id is not None:
                    assignee_ref = await deps.owners.team_ref(session, issue.assignee_team_id)
            active_exception: IssueExceptionRow | None = await session.scalar(
                select(IssueExceptionRow)
                .where(
                    IssueExceptionRow.issue_id == issue.id,
                    IssueExceptionRow.status.in_(
                        (ExceptionStatus.REQUESTED.value, ExceptionStatus.ACTIVE.value)
                    ),
                )
                .order_by(IssueExceptionRow.created_at.desc())
            )
        if issue is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        return _response(
            _issue_detail_json(
                issue,
                assignee_ref,
                issue.owner_ref is not None,
                owner_member_ref,
                active_exception,
            )
        )

    @router.get("/api/issues/{issue_id}/occurrences")
    async def issue_occurrences(
        issue_id: str,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
    ) -> Response:
        """Return linked credential occurrences without raw identity keys."""
        del principal
        async with deps.ctx.sessions() as session:
            if await deps.service.repository.by_id(session, issue_id) is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
            result: OccurrenceResult = await linked(session, issue_id)
        values: list[JsonValue] = []
        for item in result.items:
            values.append(
                json_object(
                    (
                        ("issue_id", json_str(item.issue_id)),
                        ("short_id", json_str(item.short_id)),
                        ("source", json_str(item.source)),
                        ("display_locator", json_str(item.display_locator)),
                        ("status", json_str(item.status.value)),
                        ("severity", json_str(item.severity.value)),
                        ("exposure_visibility", json_str(item.exposure_visibility.value)),
                    )
                )
            )
        return _response(
            json_object(
                (
                    ("items", json_array(tuple(values))),
                    (
                        "key_versions",
                        json_array(tuple(json_int(value) for value in result.key_versions)),
                    ),
                )
            )
        )

    @router.post("/api/issues/{issue_id}/remediation")
    async def remediate_issue(
        issue_id: str,
        body: RemediationBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.WRITE))],
    ) -> Response:
        """Request read-only remediation and verification rescans."""
        if deps.remediation is None:
            return _unavailable()
        try:
            async with deps.ctx.sessions.begin() as session:
                receipt: RemediationReceipt = await deps.remediation.request_verification(
                    session,
                    issue_id,
                    body.action,
                    body.include_linked,
                    principal.actor_ref(),
                    body.note,
                )
        except TelescreenError as error:
            if error.code is ErrorCode.INVALID_TRANSITION:
                return _invalid_transition()
            raise
        for linked_issue_id in receipt.issue_ids:
            await deps.indexer.index_issue(linked_issue_id)
        return _response(
            json_object(
                (
                    (
                        "issue_ids",
                        json_array(tuple(json_str(value) for value in receipt.issue_ids)),
                    ),
                    ("rescans_enqueued", json_int(receipt.rescans_enqueued)),
                )
            )
        )

    @router.post("/api/issues/{issue_id}/resolve")
    async def resolve_issue(
        issue_id: str,
        body: ResolveBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.WRITE))],
    ) -> Response:
        """Resolve an issue with a reason and required note."""
        if deps.remediation is None:
            return _unavailable()
        async with deps.ctx.sessions.begin() as session:
            await deps.remediation.resolve_with_reason(
                session,
                issue_id,
                body.reason,
                body.note,
                principal.actor_ref(),
                principal.has(ApiScope.ADMIN),
            )
            issue: IssueRow | None = await deps.service.repository.by_id(session, issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        await deps.indexer.index_issue(issue_id)
        return _response(_issue_json(issue))

    @router.post("/api/issues/{issue_id}/release")
    async def release_issue(
        issue_id: str,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.WRITE))],
    ) -> Response:
        """Release a member assignment to the policy or default team."""
        if deps.remediation is None:
            return _unavailable()
        async with deps.ctx.sessions.begin() as session:
            await deps.remediation.release_to_team(session, issue_id, principal.actor_ref())
            issue: IssueRow | None = await deps.service.repository.by_id(session, issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        await deps.indexer.index_issue(issue_id)
        return _response(_issue_json(issue))

    @router.post("/api/issues/{issue_id}/exceptions", status_code=201)
    async def request_issue_exception(
        issue_id: str,
        body: ExceptionRequestBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.WRITE))],
    ) -> Response:
        """Request an exception through the issue lifecycle service."""
        if deps.remediation is None:
            return _unavailable()
        async with deps.ctx.sessions.begin() as session:
            exception_id: str = await deps.remediation.request_exception(
                session,
                issue_id,
                body.reason,
                body.duration,
                principal.actor_ref(),
                principal.has(ApiScope.ADMIN),
            )
            row: IssueExceptionRow | None = await session.get(IssueExceptionRow, exception_id)
            if row is None:
                raise TelescreenError(ErrorCode.INTERNAL_ERROR, "internal error")
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
            issue_status: str = issue.status
        await deps.indexer.index_issue(issue_id)
        return _response(
            json_object((("id", json_str(exception_id)), ("status", json_str(issue_status)))),
            status_code=201,
        )

    @router.patch("/api/issues/{issue_id}")
    async def patch_issue(
        issue_id: str,
        body: IssuePatchBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.WRITE))],
    ) -> Response:
        """Change status and/or assignee in one transaction."""
        async with deps.ctx.sessions.begin() as session:
            issue: IssueRow | None = await deps.service.repository.by_id(session, issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
            if body.status is not None:
                if body.status in (IssueStatus.RESOLVED, IssueStatus.IGNORED):
                    return _invalid_transition()
                issue = await deps.service.change_status(
                    session, issue_id, body.status, principal.actor_ref()
                )
            if body.assignee_ref is not None:
                issue = await deps.service.assign(
                    session, issue_id, body.assignee_ref, principal.actor_ref()
                )
        await deps.indexer.index_issue(issue.id)
        return _response(_issue_json(issue))

    @router.post("/api/issues/{issue_id}/comments", status_code=201)
    async def add_comment(
        issue_id: str,
        body: CommentBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.WRITE))],
    ) -> Response:
        """Add a comment that the service redacts before persistence."""
        async with deps.ctx.sessions.begin() as session:
            await deps.service.comment(session, issue_id, body.body, principal.actor_ref())
        return _response(json_object((("accepted", json_bool(True)),)), status_code=201)

    return router


def _cursor(cursor: str | None) -> tuple[str, ...]:
    """Decode an optional cursor."""
    return decode_cursor(cursor) if cursor is not None else ()


def _response(value: JsonValue, status_code: int = 200) -> Response:
    """Serialize the closed JSON model into an API response."""
    return Response(
        content=dump_json(value), status_code=status_code, media_type="application/json"
    )


def _optional_string(value: str | None) -> JsonValue:
    """Serialize an optional string without native untyped values."""
    return json_str(value) if value is not None else json_null()


def _issue_json(issue: IssueRow) -> JsonValue:
    """Serialize only safe issue columns."""
    resolved_at: str | None = (
        ensure_utc(issue.resolved_at).isoformat() if issue.resolved_at else None
    )
    return json_object(
        (
            ("id", json_str(issue.id)),
            ("number", json_int(issue.number)),
            ("short_id", json_str(f"TELE-{issue.number}")),
            ("grouping_key", json_str(issue.grouping_key)),
            ("source", json_str(issue.source)),
            ("resource_key", json_str(issue.resource_key)),
            ("display_locator", json_str(issue.display_locator)),
            ("resource_ref_json", _optional_string(issue.resource_ref_json)),
            ("collector_config_id", _optional_string(issue.collector_config_id)),
            ("display_title", json_str(issue.display_title)),
            ("rule_class", json_str(issue.rule_class)),
            ("severity", json_str(issue.severity)),
            ("base_severity", json_str(issue.base_severity)),
            ("exposure_visibility", json_str(issue.exposure_visibility)),
            ("risk_score", json_int(issue.risk_score)),
            ("policy_name", _optional_string(issue.policy_name)),
            (
                "policy_version",
                json_int(issue.policy_version) if issue.policy_version is not None else json_null(),
            ),
            ("status", json_str(issue.status)),
            ("assignee_member_id", _optional_string(issue.assignee_member_id)),
            ("assignee_team_id", _optional_string(issue.assignee_team_id)),
            ("first_seen", json_str(ensure_utc(issue.first_seen).isoformat())),
            ("last_seen", json_str(ensure_utc(issue.last_seen).isoformat())),
            ("last_checked_at", json_str(ensure_utc(issue.last_checked_at).isoformat())),
            ("sweep_covered", json_bool(issue.sweep_covered)),
            ("times_seen", json_int(issue.times_seen)),
            ("distinct_fingerprints", json_int(issue.distinct_fingerprints)),
            ("regression_count", json_int(issue.regression_count)),
            ("resolved_at", _optional_string(resolved_at)),
            ("resolution", _optional_string(issue.resolution)),
        )
    )


def _issue_detail_json(
    issue: IssueRow,
    assignee_ref: str | None,
    owner_present: bool,
    owner_member_ref: str | None,
    active_exception: IssueExceptionRow | None,
) -> JsonValue:
    """Serialize issue detail with safe exposure and owner projections."""
    members: list[tuple[str, JsonValue]] = [
        (member.name, member.value) for member in _issue_json(issue).members
    ]
    active_exception_json: JsonValue = json_null()
    if active_exception is not None:
        active_exception_json = json_object(
            (
                ("id", json_str(active_exception.id)),
                ("status", json_str(active_exception.status)),
                ("duration", json_str(active_exception.duration)),
                ("expires_at", _timestamp(active_exception.expires_at)),
                ("approver_ref", _optional_string(active_exception.approver_ref)),
            )
        )
    members.extend(
        (
            ("exposure", _safe_exposure(issue.exposure_json)),
            ("exposure_changed_at", _timestamp(issue.exposure_changed_at)),
            ("asset_tier", _optional_string(issue.asset_tier)),
            ("assignee_ref", _optional_string(assignee_ref)),
            (
                "owner",
                json_object(
                    (
                        ("present", json_bool(owner_present)),
                        ("member_ref", _optional_string(owner_member_ref)),
                    )
                ),
            ),
            ("active_exception", active_exception_json),
        )
    )
    return json_object(members)


def _safe_exposure(text: str | None) -> JsonValue:
    """Project persisted exposure JSON to its closed enum/count shape."""
    if text is None:
        return json_null()
    value: JsonValue = parse_json(text)
    names: tuple[str, ...] = (
        "visibility",
        "external_principal_count",
        "shared_drive",
        "channel_kind",
        "repo_visibility",
        "is_fork",
    )
    return json_object(
        tuple((name, child) for name in names if (child := value.get(name)) is not None)
    )


def _timestamp(value: datetime | None) -> JsonValue:
    """Serialize an optional UTC timestamp."""
    return json_str(ensure_utc(value).isoformat()) if value is not None else json_null()


def _unavailable() -> Response:
    """Return the static feature-off dependency response."""
    return _response(
        json_object(
            (
                (
                    "error",
                    json_object(
                        (
                            ("code", json_str("internal_error")),
                            ("detail", json_str("internal error")),
                        )
                    ),
                ),
            )
        ),
        status_code=503,
    )


def _invalid_transition() -> Response:
    """Return the v1.1 422 response for dedicated-transition-only operations."""
    return _response(
        json_object(
            (
                (
                    "error",
                    json_object(
                        (
                            ("code", json_str("invalid_transition")),
                            ("detail", json_str("invalid issue transition")),
                        )
                    ),
                ),
            )
        ),
        status_code=422,
    )


def _finding_json(finding: FindingRow) -> JsonValue:
    """Serialize a masked finding observation."""
    return json_object(
        (
            ("id", json_str(finding.id)),
            ("issue_id", json_str(finding.issue_id)),
            ("scan_job_id", _optional_string(finding.scan_job_id)),
            ("source", json_str(finding.source)),
            ("resource_key", json_str(finding.resource_key)),
            ("display_locator", json_str(finding.display_locator)),
            ("location", json_str(finding.location)),
            ("rule_id", json_str(finding.rule_id)),
            ("rule_class", json_str(finding.rule_class)),
            ("detector", json_str(finding.detector)),
            ("confidence", json_float(finding.confidence)),
            ("masked_snippet", json_str(finding.masked_snippet)),
            ("fingerprint", json_str(finding.fingerprint)),
            ("fingerprint_key_id", json_str(finding.fingerprint_key_id)),
            ("observed_at", json_str(ensure_utc(finding.observed_at).isoformat())),
        )
    )


def _history_json(entry: IssueHistoryRow) -> JsonValue:
    """Serialize masked issue history."""
    return json_object(
        (
            ("id", json_str(entry.id)),
            ("issue_id", json_str(entry.issue_id)),
            ("kind", json_str(entry.kind)),
            ("actor_ref", json_str(entry.actor_ref)),
            ("from_value", _optional_string(entry.from_value)),
            ("to_value", _optional_string(entry.to_value)),
            ("note", _optional_string(entry.note)),
            ("at", json_str(ensure_utc(entry.at).isoformat())),
        )
    )


def _bucket_start(value: datetime, bucket: str) -> datetime:
    """Normalize a timestamp to its hourly or daily bucket."""
    if bucket == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.replace(minute=0, second=0, microsecond=0)
