"""REST endpoints for member roles and machine API tokens."""

from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.clock import ensure_utc
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
from telescreen.core.permissions import ROLE_PERMISSIONS, SCOPE_PERMISSIONS, Permission, parse_role
from telescreen.core.vocabulary import ApiScope, ErrorCode, Role, RoleChangeSource
from telescreen.db.models import MemberRow
from telescreen.directory.roles import RoleService, UserView
from telescreen.directory.tokens import CreatedToken, TokenService, TokenView


@pydantic.dataclasses.dataclass(frozen=True)
class UserRoleBody:
    """Requested role assignment; null and none clear the role."""

    role: str | None


@pydantic.dataclasses.dataclass(frozen=True)
class TokenCreateBody:
    """New machine token metadata and optional member binding."""

    label: str
    scopes: list[str]
    member_handle: str | None = None


@dataclass(frozen=True)
class UsersRouterDeps:
    """Services required by user and token routes."""

    ctx: ApiContext
    roles: RoleService
    tokens: TokenService
    idp_role_mapping: bool


def _user_json(user: UserView) -> JsonValue:
    """Build a safe member response without identity or token data."""
    return json_object(
        (
            ("handle", json_str(user.handle)),
            ("display_name", json_str(user.display_name)),
            ("email", json_str(user.email) if user.email is not None else json_null()),
            ("role", json_str(user.role.value) if user.role is not None else json_null()),
            ("idp_managed", json_bool(user.idp_managed)),
            ("active", json_bool(user.active)),
            (
                "last_sign_in_at",
                json_str(ensure_utc(user.last_sign_in_at).isoformat())
                if user.last_sign_in_at is not None
                else json_null(),
            ),
            ("teams", json_array(tuple(json_str(team) for team in user.teams))),
        )
    )


def _token_json(token: TokenView) -> JsonValue:
    """Build a safe token metadata response without token material or digest."""
    return json_object(
        (
            ("id", json_str(token.id)),
            ("label", json_str(token.label)),
            ("scopes", json_array(tuple(json_str(scope) for scope in token.scopes))),
            (
                "member_handle",
                json_str(token.member_handle) if token.member_handle is not None else json_null(),
            ),
            ("created_at", json_str(ensure_utc(token.created_at).isoformat())),
            (
                "revoked_at",
                json_str(ensure_utc(token.revoked_at).isoformat())
                if token.revoked_at is not None
                else json_null(),
            ),
        )
    )


def build_users_router(deps: UsersRouterDeps) -> APIRouter:
    """Build authenticated user, role and API-token endpoints."""
    router: APIRouter = APIRouter()

    @router.get("/api/users")
    async def list_users(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.USERS_READ))
        ],
    ) -> Response:
        """List members with their access role and team memberships."""
        del principal
        async with deps.ctx.sessions() as session:
            users: tuple[UserView, ...] = await deps.roles.list_users(
                session, deps.idp_role_mapping
            )
        return Response(
            content=dump_json(
                json_object(
                    (
                        ("items", json_array(tuple(_user_json(user) for user in users))),
                        ("next_cursor", json_null()),
                    )
                )
            ),
            media_type="application/json",
        )

    @router.get("/api/roles")
    async def list_roles(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.USERS_READ))
        ],
    ) -> JSONResponse:
        """Return sorted role and legacy-scope permission matrices."""
        del principal
        return JSONResponse(
            content={
                "roles": [
                    {
                        "role": role.value,
                        "permissions": sorted(permission.value for permission in permissions),
                    }
                    for role, permissions in sorted(
                        ROLE_PERMISSIONS, key=lambda item: item[0].value
                    )
                ],
                "scopes": [
                    {
                        "scope": scope.value,
                        "permissions": sorted(permission.value for permission in permissions),
                    }
                    for scope, permissions in sorted(
                        SCOPE_PERMISSIONS, key=lambda item: item[0].value
                    )
                ],
            }
        )

    @router.put("/api/users/{handle}/role")
    async def set_user_role(
        handle: str,
        body: UserRoleBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.USERS_WRITE))
        ],
    ) -> Response:
        """Set a member's manually managed role and audit the change."""
        role_text: str = body.role if body.role is not None else "none"
        role: Role | None = parse_role(role_text)
        async with deps.ctx.sessions.begin() as session:
            member: MemberRow = await deps.roles.set_role(
                session, handle, role, principal.actor_ref(), RoleChangeSource.MANUAL
            )
            users: tuple[UserView, ...] = await deps.roles.list_users(
                session, deps.idp_role_mapping
            )
        user: UserView | None = next((item for item in users if item.handle == member.handle), None)
        if user is None:
            raise TelescreenError(ErrorCode.INTERNAL_ERROR, "member view unavailable")
        return Response(content=dump_json(_user_json(user)), media_type="application/json")

    @router.get("/api/tokens")
    async def list_tokens(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.TOKENS_READ))
        ],
    ) -> Response:
        """List token labels and scope metadata without credential material."""
        del principal
        async with deps.ctx.sessions() as session:
            tokens: tuple[TokenView, ...] = await deps.tokens.list_tokens(session)
        return Response(
            content=dump_json(
                json_object(
                    (
                        ("items", json_array(tuple(_token_json(token) for token in tokens))),
                        ("next_cursor", json_null()),
                    )
                )
            ),
            media_type="application/json",
        )

    @router.post("/api/tokens", status_code=201)
    async def create_token(
        body: TokenCreateBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.TOKENS_WRITE))
        ],
    ) -> JSONResponse:
        """Create a bearer token and return its raw value exactly once."""
        if not body.scopes:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "at least one scope is required")
        scopes: set[ApiScope] = set()
        try:
            scopes.update(ApiScope(scope) for scope in body.scopes)
        except ValueError:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "unknown API scope") from None
        if len(scopes) != len(body.scopes):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "duplicate API scope")
        member_id: str | None = None
        async with deps.ctx.sessions.begin() as session:
            if body.member_handle is not None:
                member_id = await deps.tokens.member_id_for_handle(session, body.member_handle)
            created: CreatedToken = await deps.tokens.create(
                session,
                body.label,
                frozenset(scopes),
                member_id,
                principal.actor_ref(),
            )
        return JSONResponse(
            status_code=201, content={"id": created.token_id, "token": created.token}
        )

    @router.post("/api/tokens/{token_id}/revoke", status_code=204)
    async def revoke_token(
        token_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.TOKENS_WRITE))
        ],
    ) -> Response:
        """Revoke an API token without returning any credential data."""
        async with deps.ctx.sessions.begin() as session:
            await deps.tokens.revoke(session, token_id, principal.actor_ref())
        return Response(status_code=204)

    return router
