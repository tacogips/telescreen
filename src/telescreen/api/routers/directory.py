"""Member and team directory endpoints."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.api.common.pagination import (
    PageParams,
    decode_cursor,
    encode_cursor,
    page_params,
)
from telescreen.core.errors import TelescreenError
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode, IdentityKind
from telescreen.db.models import MemberIdentityRow, MemberRow, TeamRow
from telescreen.directory.identities import IdentityService, IdentityView
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
    lead: str | None = None


@pydantic.dataclasses.dataclass
class TeamLeadBody:
    """Validated team lead assignment."""

    lead: str | None


@pydantic.dataclasses.dataclass
class IdentityBody:
    """Validated identity input; its transient value is omitted from repr."""

    kind: IdentityKind
    value: str = field(repr=False)


@pydantic.dataclasses.dataclass
class TeamMembersBody:
    """Validated replacement membership list."""

    handles: list[str]


@dataclass(frozen=True)
class DirectoryRouterDeps:
    """Services required by directory routes."""

    ctx: ApiContext
    directory: DirectoryService
    identities: IdentityService | None = None


def build_directory_router(deps: DirectoryRouterDeps) -> APIRouter:
    """Build authenticated directory endpoints."""
    router: APIRouter = APIRouter()

    @router.get("/api/members")
    async def list_members(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DIRECTORY_READ))
        ],
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
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DIRECTORY_READ))
        ],
        page: Annotated[PageParams, Depends(page_params)],
    ) -> JSONResponse:
        """Return directory teams."""
        del principal
        async with deps.ctx.sessions() as session:
            teams: Sequence[TeamRow] = await deps.directory.list_teams(session)
            lead_rows: list[tuple[str, str | None]] = []
            for team_row in teams:
                lead_rows.append((team_row.id, await _lead_handle(session, team_row)))
            leads: tuple[tuple[str, str | None], ...] = tuple(lead_rows)
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
                        "lead": _lead_value(leads, row.id),
                    }
                    for row in items
                ],
                "next_cursor": next_cursor,
            }
        )

    @router.post("/api/members", status_code=201)
    async def create_member(
        body: MemberBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DIRECTORY_WRITE))
        ],
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
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DIRECTORY_WRITE))
        ],
    ) -> JSONResponse:
        """Create a directory team."""
        del principal
        async with deps.ctx.sessions.begin() as session:
            row: TeamRow = await deps.directory.create_team(
                session,
                body.slug,
                body.name,
                body.email,
                body.slack_channel,
                body.lead,
            )
        return JSONResponse(
            status_code=201,
            content={
                "id": row.id,
                "slug": row.slug,
                "name": row.name,
                "email": row.email,
                "slack_channel": row.slack_channel,
                "lead": body.lead,
            },
        )

    @router.put("/api/teams/{slug}/members", status_code=204)
    async def set_team_members(
        slug: str,
        body: TeamMembersBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DIRECTORY_WRITE))
        ],
    ) -> Response:
        """Replace a team's member set."""
        del principal
        async with deps.ctx.sessions.begin() as session:
            await deps.directory.set_team_members(session, slug, body.handles)
        return Response(status_code=204)

    @router.put("/api/teams/{slug}/lead", status_code=204)
    async def set_team_lead(
        slug: str,
        body: TeamLeadBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DIRECTORY_WRITE))
        ],
    ) -> Response:
        """Set or clear a team's active lead."""
        async with deps.ctx.sessions.begin() as session:
            await deps.directory.set_team_lead(session, slug, body.lead, principal.actor_ref())
        return Response(status_code=204)

    @router.get("/api/members/{handle}/identities")
    async def list_identities(
        handle: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DIRECTORY_READ))
        ],
    ) -> JSONResponse:
        """List safe identity metadata without values or full keyed references."""
        del principal
        if deps.identities is None:
            raise HTTPException(status_code=503, detail="identity service unavailable")
        async with deps.ctx.sessions() as session:
            identities: tuple[IdentityView, ...] = await deps.identities.list(session, handle)
        return JSONResponse(
            content={
                "items": [
                    {
                        "id": identity.id,
                        "kind": identity.kind,
                        "origin": identity.origin,
                        "key_prefix": identity.key_prefix,
                        "created_at": identity.created_at.isoformat(),
                    }
                    for identity in identities
                ],
                "next_cursor": None,
            }
        )

    @router.post("/api/members/{handle}/identities", status_code=201)
    async def add_identity(
        handle: str,
        body: IdentityBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DIRECTORY_WRITE))
        ],
    ) -> JSONResponse:
        """Add a manually keyed email or GitHub login identity."""
        if deps.identities is None:
            raise HTTPException(status_code=503, detail="identity service unavailable")
        if body.kind not in (IdentityKind.EMAIL, IdentityKind.GITHUB_LOGIN):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "identity kind is not configurable")
        async with deps.ctx.sessions.begin() as session:
            row: MemberIdentityRow = await deps.identities.add(
                session, handle, body.kind, body.value, principal.actor_ref()
            )
        return JSONResponse(
            status_code=201,
            content={
                "id": row.id,
                "kind": row.kind,
                "origin": row.origin,
                "key_prefix": row.identity_key[:8],
                "created_at": row.created_at.isoformat(),
            },
        )

    @router.delete("/api/members/{handle}/identities/{identity_id}", status_code=204)
    async def remove_identity(
        handle: str,
        identity_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DIRECTORY_WRITE))
        ],
    ) -> Response:
        """Remove a manually configured identity."""
        if deps.identities is None:
            raise HTTPException(status_code=503, detail="identity service unavailable")
        async with deps.ctx.sessions.begin() as session:
            await deps.identities.remove(session, handle, identity_id, principal.actor_ref())
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


async def _lead_handle(session: AsyncSession, team: TeamRow) -> str | None:
    """Resolve a team's lead ID to its public handle."""
    if team.lead_member_id is None:
        return None
    member: MemberRow | None = await session.get(MemberRow, team.lead_member_id)
    return member.handle if member is not None else None


def _lead_value(leads: tuple[tuple[str, str | None], ...], team_id: str) -> str | None:
    """Find a projected team lead handle."""
    return next((lead for candidate, lead in leads if candidate == team_id), None)
