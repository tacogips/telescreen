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
    IssuesRouterDeps,
    _cursor,
    _finding_json,
    _history_json,
    _issue_detail_json,
    _optional_string,
    _response,
)
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_array, json_int, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode, ExceptionStatus
from telescreen.db.models import FindingRow, IssueExceptionRow, IssueHistoryRow, IssueRow
from telescreen.issues.occurrences import OccurrenceResult, linked


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
