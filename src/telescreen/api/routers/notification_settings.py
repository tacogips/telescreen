"""Authenticated self-service notification settings endpoints."""

from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode, NotificationCategory, NotificationPreference
from telescreen.db.models import MemberRow
from telescreen.notifications.preferences import CategorySetting, PreferenceService


@pydantic.dataclasses.dataclass(frozen=True)
class NotificationSettingInput:
    """One requested category override; null removes the stored override."""

    category: NotificationCategory
    stored: NotificationPreference | None


@pydantic.dataclasses.dataclass(frozen=True)
class NotificationSettingsBody:
    """Replacement set of explicitly stored notification preferences."""

    items: list[NotificationSettingInput]


@dataclass(frozen=True)
class NotificationSettingsRouterDeps:
    """Dependencies required by self notification settings routes."""

    ctx: ApiContext
    preferences: PreferenceService


def _member_id(principal: Principal) -> str:
    """Require a member-bound principal for personal settings."""
    if principal.member_id is None:
        raise TelescreenError(ErrorCode.FORBIDDEN, "member principal is required")
    return principal.member_id


def _items(settings: tuple[CategorySetting, ...], email: bool, slack: bool) -> JsonValue:
    """Serialize all categories without exposing member identifiers."""
    return json_array(
        tuple(
            json_object(
                (
                    ("category", json_str(setting.category.value)),
                    (
                        "stored",
                        json_str(setting.stored.value)
                        if setting.stored is not None
                        else json_null(),
                    ),
                    ("effective", json_str(setting.effective.value)),
                    (
                        "available",
                        json_object((("email", json_bool(email)), ("slack", json_bool(slack)))),
                    ),
                )
            )
            for setting in settings
        )
    )


def build_notification_settings_router(deps: NotificationSettingsRouterDeps) -> APIRouter:
    """Build permission-protected GET and PUT notification preference routes."""
    router: APIRouter = APIRouter()

    @router.get("/api/auth/me/notification-settings")
    async def get_notification_settings(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.PROFILE_SELF))
        ],
    ) -> Response:
        """Return stored and effective preferences for the authenticated member."""
        member_id: str = _member_id(principal)
        async with deps.ctx.sessions() as session:
            member: MemberRow | None = await session.get(MemberRow, member_id)
            if member is None:
                raise TelescreenError(ErrorCode.FORBIDDEN, "member principal is unavailable")
            settings: tuple[CategorySetting, ...] = await deps.preferences.settings(
                session, member_id
            )
        return Response(
            content=dump_json(
                json_object(
                    (("items", _items(settings, bool(member.email), bool(member.slack_user_id))),)
                )
            ),
            media_type="application/json",
        )

    @router.put("/api/auth/me/notification-settings")
    async def put_notification_settings(
        body: NotificationSettingsBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.PROFILE_SELF))
        ],
    ) -> Response:
        """Replace stored category overrides for the authenticated member."""
        member_id: str = _member_id(principal)
        items: tuple[tuple[NotificationCategory, NotificationPreference | None], ...] = tuple(
            (item.category, item.stored) for item in body.items
        )
        async with deps.ctx.sessions.begin() as session:
            member: MemberRow | None = await session.get(MemberRow, member_id)
            if member is None:
                raise TelescreenError(ErrorCode.FORBIDDEN, "member principal is unavailable")
            try:
                settings: tuple[CategorySetting, ...] = await deps.preferences.update(
                    session, member_id, items
                )
            except TelescreenError as error:
                if error.code is not ErrorCode.RECIPIENT_UNREACHABLE:
                    raise
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": ErrorCode.RECIPIENT_UNREACHABLE.value,
                            "detail": "notification channel is unavailable",
                        }
                    },
                )
        return Response(
            content=dump_json(
                json_object(
                    (("items", _items(settings, bool(member.email), bool(member.slack_user_id))),)
                )
            ),
            media_type="application/json",
        )

    return router
