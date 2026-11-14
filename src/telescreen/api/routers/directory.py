"""Member and team directory endpoints."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import Principal, require_scope
from telescreen.api.common.context import ApiContext
from telescreen.api.common.pagination import (
    PageParams,
    decode_cursor,
    encode_cursor,
    page_params,
)
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ApiScope, ErrorCode
from telescreen.db.models import MemberRow, TeamRow
from telescreen.directory.service import DirectoryService


@pydantic.dataclasses.dataclass
class MemberBody:
    """Validated member creation fields."""

    handle: str
    display_name: str
    email: str | None = None
    slack_user_id: str | None = None


@pydantic.dataclasses.dataclass
class TeamBody:
    """Validated team creation fields."""

    slug: str
    name: str
    email: str | None = None
    slack_channel: str | None = None


@pydantic.dataclasses.dataclass
class TeamMembersBody:
    """Validated replacement membership list."""

    handles: list[str]


@dataclass(frozen=True)
class DirectoryRouterDeps:
    """Services required by directory routes."""

    ctx: ApiContext
    directory: DirectoryService


def build_directory_router(deps: DirectoryRouterDeps) -> APIRouter:
    """Build authenticated directory endpoints."""
    router: APIRouter = APIRouter()

    @router.get("/api/members")
    async def list_members(
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
        page: Annotated[PageParams, Depends(page_params)],
    ) -> JSONResponse:
        """Return directory members without credentials or token material."""
        del principal
        async with deps.ctx.sessions() as session:
            members: Sequence[MemberRow] = await deps.directory.list_members(session)
        after: str | None = _cursor_value(page.cursor)
        selected: list[MemberRow] = [row for row in members if after is None or row.handle > after]
        items: list[MemberRow] = selected[: page.limit]
        next_cursor: str | None = (
            encode_cursor((items[-1].handle,)) if len(selected) > page.limit and items else None
        )
        return JSONResponse(
            content={
                "items": [
                    {
                        "id": row.id,
                        "handle": row.handle,
                        "display_name": row.display_name,
                        "email": row.email,
                        "slack_user_id": row.slack_user_id,
                        "active": row.active,
                    }
                    for row in items
                ],
                "next_cursor": next_cursor,
            }
        )

    @router.get("/api/teams")
    async def list_teams(
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
        page: Annotated[PageParams, Depends(page_params)],
    ) -> JSONResponse:
        """Return directory teams."""
        del principal
        async with deps.ctx.sessions() as session:
            teams: Sequence[TeamRow] = await deps.directory.list_teams(session)
        after: str | None = _cursor_value(page.cursor)
        selected: list[TeamRow] = [row for row in teams if after is None or row.slug > after]
        items: list[TeamRow] = selected[: page.limit]
        next_cursor: str | None = (
            encode_cursor((items[-1].slug,)) if len(selected) > page.limit and items else None
        )
        return JSONResponse(
            content={
                "items": [
                    {
                        "id": row.id,
                        "slug": row.slug,
                        "name": row.name,
                        "email": row.email,
                        "slack_channel": row.slack_channel,
                    }
                    for row in items
                ],
                "next_cursor": next_cursor,
            }
        )

    @router.post("/api/members", status_code=201)
    async def create_member(
        body: MemberBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.ADMIN))],
    ) -> JSONResponse:
        """Create a directory member."""
        del principal
        async with deps.ctx.sessions.begin() as session:
            row: MemberRow = await deps.directory.create_member(
                session, body.handle, body.display_name, body.email, body.slack_user_id
            )
        return JSONResponse(
            status_code=201,
            content={
                "id": row.id,
                "handle": row.handle,
                "display_name": row.display_name,
                "email": row.email,
                "slack_user_id": row.slack_user_id,
                "active": row.active,
            },
        )

    @router.post("/api/teams", status_code=201)
    async def create_team(
        body: TeamBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.ADMIN))],
    ) -> JSONResponse:
        """Create a directory team."""
        del principal
        async with deps.ctx.sessions.begin() as session:
            row: TeamRow = await deps.directory.create_team(
                session, body.slug, body.name, body.email, body.slack_channel
            )
        return JSONResponse(
            status_code=201,
            content={
                "id": row.id,
                "slug": row.slug,
                "name": row.name,
                "email": row.email,
                "slack_channel": row.slack_channel,
            },
        )

    @router.put("/api/teams/{slug}/members", status_code=204)
    async def set_team_members(
        slug: str,
        body: TeamMembersBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.ADMIN))],
    ) -> Response:
        """Replace a team's member set."""
        del principal
        async with deps.ctx.sessions.begin() as session:
            await deps.directory.set_team_members(session, slug, body.handles)
        return Response(status_code=204)

    return router


def _cursor_value(cursor: str | None) -> str | None:
    """Decode a directory cursor with exactly one stable ordering key."""
    if cursor is None:
        return None
    parts: tuple[str, ...] = decode_cursor(cursor)
    if len(parts) != 1:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
    return parts[0]
