"""Archive and review issue triage routes."""

from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.issues_common import _issue_json, _response
from telescreen.core.errors import TelescreenError
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ArchiveKind, ErrorCode
from telescreen.db.models import IssueRow
from telescreen.issues.archive import ArchiveRequest, archive_issue, unarchive_issue
from telescreen.issues.service import IssueService
from telescreen.ports.search import SearchIndexer


@pydantic.dataclasses.dataclass(frozen=True)
class ArchiveBody:
    """Typed archive condition submitted by a triage client."""

    kind: ArchiveKind
    duration_minutes: int | None = None
    count: int | None = None


@dataclass(frozen=True)
class IssueTriageRouterDeps:
    """Dependencies needed by archive and review routes."""

    ctx: ApiContext
    service: IssueService
    indexer: SearchIndexer


def build_issue_triage_router(deps: IssueTriageRouterDeps) -> APIRouter:
    """Build archive, unarchive and mark-reviewed endpoints."""
    router: APIRouter = APIRouter()

    @router.post("/api/issues/{issue_id}/archive")
    async def archive(
        issue_id: str,
        body: ArchiveBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
    ) -> Response:
        """Archive an open issue under a bounded condition."""
        async with deps.ctx.sessions.begin() as session:
            issue: IssueRow | None = await deps.service.repository.by_id(session, issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
            await archive_issue(
                session,
                issue,
                ArchiveRequest(body.kind, body.duration_minutes, body.count),
                principal.actor_ref(),
                deps.service.clock.now(),
                deps.service.archive_policy,
                deps.service.audit,
                deps.service.activity,
            )
        await deps.indexer.index_issue(issue_id)
        return _response(_issue_json(issue))

    @router.post("/api/issues/{issue_id}/unarchive")
    async def unarchive(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
    ) -> Response:
        """Return an archived issue to unresolved."""
        async with deps.ctx.sessions.begin() as session:
            issue: IssueRow | None = await deps.service.repository.by_id(session, issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
            await unarchive_issue(
                session,
                issue,
                principal.actor_ref(),
                deps.service.clock.now(),
                deps.service.audit,
                deps.service.activity,
            )
        await deps.indexer.index_issue(issue_id)
        return _response(_issue_json(issue))

    @router.post("/api/issues/{issue_id}/review")
    async def review(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
    ) -> Response:
        """Clear the For review flag for a human triage action."""
        async with deps.ctx.sessions.begin() as session:
            issue: IssueRow = await deps.service.mark_reviewed(
                session, issue_id, principal.actor_ref()
            )
        await deps.indexer.index_issue(issue_id)
        return _response(_issue_json(issue))

    return router
