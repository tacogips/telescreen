"""Exception request and approval REST endpoints."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from telescreen.api.common.auth import Principal, require_scope
from telescreen.api.common.context import ApiContext
from telescreen.api.common.pagination import PageParams, decode_cursor, encode_cursor, page_params
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, dump_json, json_array, json_null, json_object, json_str
from telescreen.core.vocabulary import ApiScope, ErrorCode, ExceptionStatus
from telescreen.db.models import IssueExceptionRow
from telescreen.issues.exceptions import ExceptionService
from telescreen.ports.directory import OwnerDirectory
from telescreen.ports.search import SearchIndexer


@pydantic.dataclasses.dataclass(frozen=True)
class ExceptionDecisionBody:
    """Optional safe note for an exception decision."""

    note: str | None = None


@dataclass(frozen=True)
class ExceptionsRouterDeps:
    """Dependencies required by exception routes."""

    ctx: ApiContext
    service: ExceptionService | None = None
    indexer: SearchIndexer | None = None
    owners: OwnerDirectory | None = None


def build_exceptions_router(deps: ExceptionsRouterDeps) -> APIRouter:
    """Build authenticated exception list and lifecycle endpoints."""
    router: APIRouter = APIRouter()

    @router.get("/api/exceptions")
    async def list_exceptions(
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
        page: Annotated[PageParams, Depends(page_params)],
        status: ExceptionStatus | None = None,
        issue_id: str | None = None,
        approver: str | None = None,
    ) -> Response:
        """List exception requests with an opaque keyset cursor."""
        if deps.service is None:
            return _unavailable()
        approver_ref: str | None = approver
        async with deps.ctx.sessions() as session:
            if approver == "me":
                if principal.member_id is None:
                    raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
                if deps.owners is None:
                    return _unavailable()
                approver_ref = await deps.owners.member_ref(session, principal.member_id)
                if approver_ref is None:
                    raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
            rows: tuple[IssueExceptionRow, ...]
            next_id: str | None
            rows, next_id = await deps.service.list(
                session, status, issue_id, approver_ref, page.limit, _decode(page.cursor)
            )
        next_cursor: str | None = encode_cursor((next_id,)) if next_id is not None else None
        return _response(
            json_object(
                (
                    ("items", json_array(tuple(_exception_json(row) for row in rows))),
                    ("next_cursor", _optional_string(next_cursor)),
                )
            )
        )

    @router.post("/api/exceptions/{exception_id}/approve")
    async def approve_exception(
        exception_id: str,
        body: ExceptionDecisionBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.WRITE))],
    ) -> Response:
        """Approve a pending exception and reindex its issue after commit."""
        return await _decide(deps, exception_id, principal, "approve", body.note)

    @router.post("/api/exceptions/{exception_id}/reject")
    async def reject_exception(
        exception_id: str,
        body: ExceptionDecisionBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.WRITE))],
    ) -> Response:
        """Reject a pending exception and reindex its issue after commit."""
        return await _decide(deps, exception_id, principal, "reject", body.note)

    @router.post("/api/exceptions/{exception_id}/revoke")
    async def revoke_exception(
        exception_id: str,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.WRITE))],
    ) -> Response:
        """Revoke an active exception and reindex its issue after commit."""
        return await _decide(deps, exception_id, principal, "revoke", None)

    return router


async def _decide(
    deps: ExceptionsRouterDeps,
    exception_id: str,
    principal: Principal,
    action: str,
    note: str | None,
) -> Response:
    """Execute one service decision in a transaction and index after commit."""
    if deps.service is None or deps.indexer is None:
        return _unavailable()
    issue_id: str
    async with deps.ctx.sessions.begin() as session:
        row: IssueExceptionRow | None = await session.get(IssueExceptionRow, exception_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "exception not found")
        issue_id = row.issue_id
        actor_ref: str = principal.actor_ref()
        member_id: str | None = principal.member_id
        admin: bool = principal.has(ApiScope.ADMIN)
        if action == "approve":
            await deps.service.approve(session, exception_id, actor_ref, member_id, admin, note)
        elif action == "reject":
            await deps.service.reject(session, exception_id, actor_ref, member_id, admin, note)
        else:
            await deps.service.revoke(session, exception_id, actor_ref, member_id, admin)
    await deps.indexer.index_issue(issue_id)
    return _response(json_object((("id", json_str(exception_id)), ("status", json_str(action)))))


def _exception_json(row: IssueExceptionRow) -> JsonValue:
    """Serialize exception metadata with only stored redacted reason text."""
    return json_object(
        (
            ("id", json_str(row.id)),
            ("issue_id", json_str(row.issue_id)),
            ("reason", json_str(row.reason)),
            ("duration", json_str(row.duration)),
            ("status", json_str(row.status)),
            ("requested_by", json_str(row.requested_by)),
            ("approver_ref", _optional_string(row.approver_ref)),
            ("decided_by", _optional_string(row.decided_by)),
            ("decided_at", _timestamp(row.decided_at)),
            ("expires_at", _timestamp(row.expires_at)),
            ("created_at", json_str(ensure_utc(row.created_at).isoformat())),
        )
    )


def _decode(cursor: str | None) -> str | None:
    """Decode the single id marker accepted by the service cursor."""
    if cursor is None:
        return None
    values: tuple[str, ...] = decode_cursor(cursor)
    if len(values) != 1:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
    return values[0]


def _timestamp(value: datetime | None) -> JsonValue:
    """Serialize an optional UTC timestamp."""
    return json_str(ensure_utc(value).isoformat()) if value is not None else json_null()


def _optional_string(value: str | None) -> JsonValue:
    """Serialize an optional string."""
    return json_str(value) if value is not None else json_null()


def _response(value: JsonValue, status_code: int = 200) -> Response:
    """Serialize the closed JSON model into a response."""
    return Response(
        content=dump_json(value), status_code=status_code, media_type="application/json"
    )


def _unavailable() -> Response:
    """Return a static feature-off dependency error."""
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
