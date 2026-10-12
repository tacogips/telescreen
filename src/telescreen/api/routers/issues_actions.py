"""Issue actions API routes."""

from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.routers.issues_common import (
    IssuesRouterDeps,
    _invalid_transition,
    _issue_json,
    _response,
    _unavailable,
)
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_array, json_int, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import (
    ErrorCode,
    ExceptionDuration,
    IssueResolution,
    IssueStatus,
    RemediationAction,
)
from telescreen.db.models import IssueExceptionRow, IssueRow
from telescreen.ports.issues import RemediationReceipt


@pydantic.dataclasses.dataclass
class IssuePatchBody:
    """Validated issue status and assignment patch."""

    status: IssueStatus | None = None
    assignee_ref: str | None = None


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


def build_issues_actions_router(deps: IssuesRouterDeps) -> APIRouter:
    """Build the actions issue API routes."""
    router: APIRouter = APIRouter()

    @router.post("/api/issues/{issue_id}/remediation")
    async def remediate_issue(
        issue_id: str,
        body: RemediationBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
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
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
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
                principal.allows(Permission.ISSUES_RESOLVE_UNVERIFIED),
            )
            issue: IssueRow | None = await deps.service.repository.by_id(session, issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        await deps.indexer.index_issue(issue_id)
        return _response(_issue_json(issue))

    @router.post("/api/issues/{issue_id}/release")
    async def release_issue(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
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
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.EXCEPTIONS_WRITE))
        ],
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
                principal.allows(Permission.EXCEPTIONS_MANAGE_ANY),
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
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
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

    return router
