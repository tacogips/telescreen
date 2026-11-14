"""Public OIDC configuration and authenticated session introspection endpoints."""

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import Principal, declare_external, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.errors import TelescreenError
from telescreen.core.oidc_settings import OidcSettings
from telescreen.core.permissions import Permission, role_permissions
from telescreen.core.vocabulary import ErrorCode, ExternalAuthKind, Role
from telescreen.db.models import MemberRow
from telescreen.identity.signin import SignInService
from telescreen.ports.identity import BearerVerifier, ProviderMetadata, SignInResult

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthRouterDeps:
    """Dependencies required by public auth and self-service routes."""

    ctx: ApiContext
    oidc: OidcSettings | None
    ui_token_login: bool
    verifier: BearerVerifier | None
    sign_in: SignInService | None


def build_auth_router(deps: AuthRouterDeps) -> APIRouter:
    """Build auth configuration, OIDC sign-in and current-principal endpoints."""
    router: APIRouter = APIRouter()

    @router.get("/api/auth/config")
    async def auth_config(
        external: Annotated[None, Depends(declare_external(ExternalAuthKind.PUBLIC))],
    ) -> JSONResponse:
        """Return public login metadata, falling back safely if discovery is down."""
        del external
        if deps.oidc is None or deps.verifier is None:
            return JSONResponse(content={"oidc": None, "token_login": deps.ui_token_login})
        try:
            metadata: ProviderMetadata = await deps.verifier.metadata()
        except TelescreenError as error:
            if error.code is not ErrorCode.IDP_UNAVAILABLE:
                raise
            logger.warning("OIDC metadata unavailable; token login remains available")
            return JSONResponse(content={"oidc": None, "token_login": deps.ui_token_login})
        return JSONResponse(
            content={
                "oidc": {
                    "issuer": deps.oidc.issuer,
                    "client_id": deps.oidc.client_id,
                    "authorization_endpoint": metadata.authorization_endpoint,
                    "token_endpoint": metadata.token_endpoint,
                    "end_session_endpoint": metadata.end_session_endpoint,
                    "scopes": list(deps.oidc.scopes),
                },
                "token_login": deps.ui_token_login,
            }
        )

    @router.post("/api/auth/session")
    async def create_session(
        request: Request,
        external: Annotated[None, Depends(declare_external(ExternalAuthKind.OIDC_SIGN_IN))],
    ) -> JSONResponse:
        """Exchange a verified OIDC access token for safe member metadata."""
        del external
        if deps.oidc is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "OIDC sign-in is not configured")
        if deps.sign_in is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "OIDC sign-in is not configured")
        authorization: str | None = request.headers.get("authorization")
        if authorization is None or not authorization.startswith("Bearer "):
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        token: str = authorization[7:]
        if not token:
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        result: SignInResult = await deps.sign_in.sign_in(token)
        role: Role | None = result.member.role
        if role is None:
            raise TelescreenError(ErrorCode.FORBIDDEN, "permission is not granted")
        return JSONResponse(
            content={
                "member": {
                    "handle": result.member.handle,
                    "display_name": result.member.display_name,
                },
                "role": role.value,
                "permissions": sorted(permission.value for permission in role_permissions(role)),
            }
        )

    @router.get("/api/auth/me")
    async def current_session(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.AUTH_SELF))
        ],
    ) -> JSONResponse:
        """Return the authenticated principal's safe identity and permission summary."""
        member: MemberRow | None = None
        if principal.member_id is not None:
            async with deps.ctx.sessions() as session:
                member = await session.get(MemberRow, principal.member_id)
            if member is None:
                raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        return JSONResponse(
            content={
                "kind": principal.kind.value,
                "member": (
                    {"handle": member.handle, "display_name": member.display_name}
                    if member is not None
                    else None
                ),
                "role": principal.role.value if principal.role is not None else None,
                "permissions": sorted(permission.value for permission in principal.permissions),
            }
        )

    return router
