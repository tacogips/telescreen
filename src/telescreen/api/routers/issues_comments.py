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
from telescreen.core.clock import ensure_utc
from telescreen.core.json import JsonValue, json_bool, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.db.models import IssueCommentRow
from telescreen.issues.activity.comments import CommentService
from telescreen.ports.directory import RecipientResolver


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
            resolver: RecipientResolver = deps.resolver or deps.service.resolver
            service: CommentService = CommentService(
                deps.service.redactor,
                deps.ctx.audit,
                deps.ctx.clock,
                deps.member_notifier,
                resolver,
                deps.actors,
                deps.service.activity,
            )
            comment: IssueCommentRow = await service.create(
                session, issue_id, body.body, principal.actor_ref()
            )
        deleted: bool = comment.deleted_at is not None
        comment_json: JsonValue = json_object(
            (
                ("id", json_str(comment.id)),
                ("body", json_str("" if deleted else comment.body)),
                ("created_at", json_str(ensure_utc(comment.created_at).isoformat())),
                ("deleted", json_bool(deleted)),
            )
        )
        return _response(comment_json, status_code=201)

    return router
