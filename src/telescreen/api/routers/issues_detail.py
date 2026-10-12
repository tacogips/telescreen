"""Issue detail API routes."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.sql import Select

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.pagination import PageParams, encode_cursor, page_params
from telescreen.api.routers.issues_common import (
    IssueItemExtras,
    IssuesRouterDeps,
    _cursor,
    _finding_json,
    _history_json,
    _issue_detail_json,
    _issue_json,
    _optional_string,
    _response,
    issue_item_extras,
)
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonValue,
    json_array,
    json_bool,
    json_int,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode, ExceptionStatus
from telescreen.db.models import (
    FindingRow,
    IssueExceptionRow,
    IssueHistoryRow,
    IssueRow,
    IssueSubscriptionRow,
)
from telescreen.issues.activity.subscriptions import subscription_for
from telescreen.issues.detail.group import group_issue_ids
from telescreen.issues.occurrences import OccurrenceResult, linked
from telescreen.ports.directory import ActorDisplay
from telescreen.ports.issues import SuggestedAssignee


def build_issues_detail_router(deps: IssuesRouterDeps) -> APIRouter:
    """Build the detail issue API routes."""
    router: APIRouter = APIRouter()

    @router.get("/api/issues/{issue_id}/events")
    async def issue_events(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
        page: Annotated[PageParams, Depends(page_params)],
        order: str = "desc",
    ) -> Response:
        """Return chronologically ordered masked findings across a merged group (B1)."""
        del principal
        if order not in ("asc", "desc"):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid event order")
        after: tuple[str, ...] = _cursor(page.cursor)
        async with deps.ctx.sessions() as session:
            issue_ids: tuple[str, ...] = await group_issue_ids(session, issue_id)
            query: Select[tuple[FindingRow]] = select(FindingRow).where(
                FindingRow.issue_id.in_(issue_ids)
            )
            if after:
                if len(after) != 2:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
                try:
                    marker_at: datetime = ensure_utc(datetime.fromisoformat(after[0]))
                except ValueError:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
                if order == "asc":
                    query = query.where(
                        (FindingRow.observed_at > marker_at)
                        | ((FindingRow.observed_at == marker_at) & (FindingRow.id > after[1]))
                    )
                else:
                    query = query.where(
                        (FindingRow.observed_at < marker_at)
                        | ((FindingRow.observed_at == marker_at) & (FindingRow.id < after[1]))
                    )
            if order == "asc":
                ordered_query: Select[tuple[FindingRow]] = query.order_by(
                    FindingRow.observed_at.asc(), FindingRow.id.asc()
                ).limit(page.limit + 1)
            else:
                ordered_query = query.order_by(
                    FindingRow.observed_at.desc(), FindingRow.id.desc()
                ).limit(page.limit + 1)
            rows: list[FindingRow] = list((await session.scalars(ordered_query)).all())
        has_more: bool = len(rows) > page.limit
        items: list[FindingRow] = rows[: page.limit]
        next_cursor: str | None = None
        if has_more and items:
            last: FindingRow = items[-1]
            next_cursor = encode_cursor((ensure_utc(last.observed_at).isoformat(), last.id))
        item_values: list[JsonValue] = []
        async with deps.ctx.sessions() as session:
            for row in items:
                finding_issue: IssueRow | None = await session.get(IssueRow, row.issue_id)
                short_id: str = f"TELE-{finding_issue.number}" if finding_issue is not None else ""
                finding_json: JsonValue = _finding_json(row)
                item_values.append(
                    json_object(
                        tuple((member.name, member.value) for member in finding_json.members)
                        + (("issue_short_id", json_str(short_id)),)
                    )
                )
        return _response(
            json_object(
                (
                    ("items", json_array(tuple(item_values))),
                    ("next_cursor", _optional_string(next_cursor)),
                )
            )
        )

    @router.get("/api/issues/{issue_id}/history")
    async def issue_history(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
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

    @router.get("/api/issues/{issue_id}")
    async def get_issue(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
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
            extras: tuple[IssueItemExtras, ...] = await issue_item_extras(
                session, (issue,), principal.member_id, deps.actors
            )
            if deps.actors is None:
                extras = (
                    IssueItemExtras(
                        None,
                        extras[0].merged_count,
                        extras[0].bookmarked,
                        extras[0].trend,
                    ),
                )
            subscription: IssueSubscriptionRow | None = (
                await subscription_for(session, issue.id, principal.member_id)
                if principal.member_id is not None
                else None
            )
            merged_into: IssueRow | None = (
                await session.get(IssueRow, issue.merged_into_id)
                if issue.merged_into_id is not None
                else None
            )
            merged_children: tuple[IssueRow, ...] = tuple(
                await session.scalars(
                    select(IssueRow)
                    .where(IssueRow.merged_into_id == issue.id)
                    .order_by(IssueRow.id)
                )
            )
            suggestions: tuple[SuggestedAssignee, ...] = (
                await deps.suggester.suggest(session, issue.id)
                if deps.suggester is not None
                else ()
            )
            suggestion_refs: tuple[str, ...] = tuple(item.ref for item in suggestions)
            suggestion_displays: tuple[ActorDisplay, ...] = (
                await deps.actors.display(session, suggestion_refs)
                if deps.actors is not None and suggestion_refs
                else ()
            )
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
            detail_json: JsonValue = _issue_detail_json(
                issue,
                assignee_ref,
                issue.owner_ref is not None,
                owner_member_ref,
                active_exception,
            )
            item_json: JsonValue = _issue_json(issue, extras[0])
            replace_names: tuple[str, ...] = ("assignee", "merged_count", "bookmarked", "trend")
            members: list[tuple[str, JsonValue]] = [
                (member.name, member.value)
                for member in detail_json.members
                if member.name not in replace_names
            ]
            members.extend(
                (member.name, member.value)
                for member in item_json.members
                if member.name in replace_names
            )
            subscription_json: JsonValue = (
                json_object(
                    (
                        ("subscribed", json_bool(subscription.subscribed)),
                        ("reason", json_str(subscription.reason)),
                    )
                )
                if subscription is not None
                else json_object((("subscribed", json_bool(False)), ("reason", json_str("manual"))))
            )
            suggestion_values: list[JsonValue] = []
            for suggestion in suggestions:
                display_name: str = next(
                    (
                        display.display_name
                        for display in suggestion_displays
                        if display.ref == suggestion.ref
                    ),
                    "Unknown",
                )
                suggestion_values.append(
                    json_object(
                        (
                            ("ref", json_str(suggestion.ref)),
                            ("display_name", json_str(display_name)),
                            ("reason", json_str(suggestion.reason.value)),
                        )
                    )
                )
            members.extend(
                (
                    ("subscription", subscription_json),
                    ("merged_into", _issue_ref_json(merged_into)),
                    (
                        "merged_children",
                        json_array(tuple(_issue_ref_json(child) for child in merged_children)),
                    ),
                    ("suggested_assignees", json_array(tuple(suggestion_values))),
                )
            )
        return _response(json_object(tuple(members)))

    @router.get("/api/issues/{issue_id}/occurrences")
    async def issue_occurrences(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
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

    return router


def _issue_ref_json(issue: IssueRow | None) -> JsonValue:
    """Serialize a merged issue reference without exposing raw locator fields."""
    if issue is None:
        return json_null()
    return json_object(
        (
            ("id", json_str(issue.id)),
            ("short_id", json_str(f"TELE-{issue.number}")),
            ("source", json_str(issue.source)),
            ("display_locator", json_str(issue.display_locator)),
            ("status", json_str(issue.status)),
        )
    )
