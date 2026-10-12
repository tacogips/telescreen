"""Issue comments API routes."""

from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.routers.issues_common import (
    IssuesRouterDeps,
    _response,
)
from telescreen.core.json import json_bool, json_object
from telescreen.core.permissions import Permission


@pydantic.dataclasses.dataclass
class CommentBody:
    """Validated user comment body."""

    body: str


def build_issues_comments_router(deps: IssuesRouterDeps) -> APIRouter:
    """Build the comments issue API routes."""
    router: APIRouter = APIRouter()

    @router.post("/api/issues/{issue_id}/comments", status_code=201)
    async def add_comment(
        issue_id: str,
        body: CommentBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
    ) -> Response:
        """Add a comment that the service redacts before persistence."""
        async with deps.ctx.sessions.begin() as session:
            await deps.service.comment(session, issue_id, body.body, principal.actor_ref())
        return _response(json_object((("accepted", json_bool(True)),)), status_code=201)

    return router
