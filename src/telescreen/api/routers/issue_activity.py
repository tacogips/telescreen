"""Unregistered comments, subscriptions, bookmarks, and activity routes."""

from datetime import datetime
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.pagination import PageParams, decode_cursor, encode_cursor, page_params
from telescreen.api.routers.issues_common import IssuesRouterDeps, _optional_string, _response
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_array, json_bool, json_null, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode, SubscriptionReason
from telescreen.db.models import IssueCommentRow, IssueSubscriptionRow
from telescreen.issues.activity.bookmarks import bookmark, unbookmark
from telescreen.issues.activity.comments import CommentService
from telescreen.issues.activity.feed import ActivityItem, ActivityPage, activity_feed
from telescreen.issues.activity.subscriptions import set_subscription
from telescreen.ports.directory import RecipientResolver


@pydantic.dataclasses.dataclass(frozen=True)
class CommentEditBody:
    """New comment text for an edit."""

    body: str


@pydantic.dataclasses.dataclass(frozen=True)
class SubscriptionBody:
    """Explicit subscription state."""

    subscribed: bool


def build_issue_activity_router(deps: IssuesRouterDeps) -> APIRouter:
    """Build direct-testable issue activity routes; wiring belongs to p12."""
    router: APIRouter = APIRouter(tags=["issue-activity"])

    @router.get("/api/issues/{issue_id}/activity")
    async def get_activity(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
        page: Annotated[PageParams, Depends(page_params)],
    ) -> Response:
        """Return the persisted activity feed with safe actor displays."""
        values: tuple[str, ...] = decode_cursor(page.cursor) if page.cursor is not None else ()
        cursor: tuple[str, str] | None = None
        if values:
            if len(values) != 2:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
            try:
                ensure_utc(datetime.fromisoformat(values[0]))
            except ValueError:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
            cursor = (values[0], values[1])
        async with deps.ctx.sessions() as session:
            can_manage: bool = Permission.COMMENTS_MANAGE_ANY in principal.permissions
            page_result: ActivityPage = await activity_feed(
                session,
                issue_id,
                cursor,
                page.limit,
                deps.actors,
                principal.actor_ref(),
                can_manage,
            )
        next_cursor: str | None = (
            encode_cursor(page_result.next_cursor) if page_result.next_cursor is not None else None
        )
        return _response(
            json_object(
                (
                    (
                        "items",
                        json_array(tuple(_activity_json(item) for item in page_result.items)),
                    ),
                    ("next_cursor", _optional_string(next_cursor)),
                )
            )
        )

    @router.patch("/api/issues/{issue_id}/comments/{comment_id}")
    async def edit_comment(
        issue_id: str,
        comment_id: str,
        body: CommentEditBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
    ) -> Response:
        """Edit an owned comment or one managed by comments.manage_any."""
        async with deps.ctx.sessions.begin() as session:
            service: CommentService = _comment_service(deps)
            row: IssueCommentRow = await service.edit(
                session,
                issue_id,
                comment_id,
                body.body,
                principal.actor_ref(),
                Permission.COMMENTS_MANAGE_ANY in principal.permissions,
            )
        return _response(_comment_json(row))

    @router.delete("/api/issues/{issue_id}/comments/{comment_id}")
    async def delete_comment(
        issue_id: str,
        comment_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_WRITE))
        ],
    ) -> Response:
        """Soft-delete an owned comment or one managed by comments.manage_any."""
        async with deps.ctx.sessions.begin() as session:
            row: IssueCommentRow = await _comment_service(deps).delete(
                session,
                issue_id,
                comment_id,
                principal.actor_ref(),
                Permission.COMMENTS_MANAGE_ANY in principal.permissions,
            )
        return _response(_comment_json(row))

    @router.put("/api/issues/{issue_id}/subscription")
    async def update_subscription(
        issue_id: str,
        body: SubscriptionBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_FOLLOW))
        ],
    ) -> Response:
        """Set a manual subscription choice for the signed-in member."""
        member_id: str = _require_member(principal)
        async with deps.ctx.sessions.begin() as session:
            row: IssueSubscriptionRow = await set_subscription(
                session,
                issue_id,
                member_id,
                body.subscribed,
                SubscriptionReason.MANUAL,
                deps.ctx.clock,
            )
        return _subscription_json(row)

    @router.put("/api/issues/{issue_id}/bookmark")
    async def add_bookmark(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_FOLLOW))
        ],
    ) -> Response:
        """Bookmark an issue for the signed-in member."""
        member_id: str = _require_member(principal)
        async with deps.ctx.sessions.begin() as session:
            await bookmark(session, issue_id, member_id, deps.ctx.clock)
        return _response(json_object((("bookmarked", json_bool(True)),)))

    @router.delete("/api/issues/{issue_id}/bookmark")
    async def remove_bookmark(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_FOLLOW))
        ],
    ) -> Response:
        """Remove an issue bookmark for the signed-in member."""
        member_id: str = _require_member(principal)
        async with deps.ctx.sessions.begin() as session:
            await unbookmark(session, issue_id, member_id)
        return _response(json_object((("bookmarked", json_bool(False)),)))

    return router


def _comment_service(deps: IssuesRouterDeps) -> CommentService:
    """Build the comment service from established issue dependencies."""
    resolver: RecipientResolver = deps.resolver or deps.service.resolver
    return CommentService(
        deps.service.redactor,
        deps.ctx.audit,
        deps.ctx.clock,
        deps.member_notifier,
        resolver,
        deps.actors,
        deps.service.activity,
    )


def _require_member(principal: Principal) -> str:
    """Require a member-bound identity for personal follow state."""
    if principal.member_id is None:
        raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
    return principal.member_id


def _comment_json(row: IssueCommentRow) -> JsonValue:
    """Serialize a persisted comment without leaking deleted content."""
    deleted: bool = row.deleted_at is not None
    return json_object(
        (
            ("id", json_str(row.id)),
            ("body", json_str("" if deleted else row.body)),
            ("created_at", json_str(ensure_utc(row.created_at).isoformat())),
            (
                "edited_at",
                _optional_string(ensure_utc(row.edited_at).isoformat() if row.edited_at else None),
            ),
            ("deleted", json_bool(deleted)),
        )
    )


def _subscription_json(row: IssueSubscriptionRow) -> Response:
    """Serialize a subscription result."""
    return _response(
        json_object((("subscribed", json_bool(row.subscribed)), ("reason", json_str(row.reason))))
    )


def _activity_json(item: ActivityItem) -> JsonValue:
    """Serialize an activity item and safe actor projection."""
    comment: JsonValue = json_null()
    if item.comment is not None:
        comment = json_object(
            (
                ("id", json_str(item.comment.id)),
                ("body", json_str(item.comment.body)),
                (
                    "edited_at",
                    _optional_string(
                        item.comment.edited_at.isoformat() if item.comment.edited_at else None
                    ),
                ),
                ("deleted", json_bool(item.comment.deleted)),
                ("can_edit", json_bool(item.comment.can_edit)),
            )
        )
    actor: JsonValue = json_object(
        (
            ("kind", json_str(item.actor.kind.value)),
            ("ref", json_str(item.actor.ref)),
            ("display_name", json_str(item.actor.display_name)),
            ("handle", _optional_string(item.actor.handle)),
            ("active", json_bool(item.actor.active)),
        )
    )
    return json_object(
        (
            ("id", json_str(item.id)),
            ("type", json_str(item.type)),
            ("kind", json_str(item.kind)),
            ("actor", actor),
            ("at", json_str(item.at.isoformat())),
            ("from_value", _optional_string(item.from_value)),
            ("to_value", _optional_string(item.to_value)),
            ("note", _optional_string(item.note)),
            ("comment", comment),
        )
    )
