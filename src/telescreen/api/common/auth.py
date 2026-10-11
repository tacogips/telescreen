"""Bearer authentication and per-route permission dependencies."""

import hashlib
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select

from telescreen.api.common.context import ApiContext
from telescreen.core.errors import TelescreenError
from telescreen.core.permissions import Permission, effective_token_permissions, role_permissions
from telescreen.core.vocabulary import ApiScope, ErrorCode, ExternalAuthKind, PrincipalKind, Role
from telescreen.db.models import ApiTokenRow, MemberRow
from telescreen.ports.identity import MemberAuth, VerifiedToken


@dataclass(frozen=True)
class Principal:
    """Authenticated API identity and its granted permissions."""

    kind: PrincipalKind
    token_id: str | None
    member_id: str | None
    role: Role | None
    permissions: frozenset[Permission]

    def allows(self, permission: Permission) -> bool:
        """Return whether this principal holds a permission."""
        return permission in self.permissions

    def actor_ref(self) -> str:
        """Return a stable, non-secret audit reference."""
        if self.member_id is not None:
            return f"member:{self.member_id}"
        return f"token:{self.token_id}"


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest used for indexed token lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _stored_role(value: str | None) -> Role | None:
    """Parse a stored member role, treating unknown values as unassigned."""
    if value is None:
        return None
    try:
        return Role(value)
    except ValueError:
        return None


async def _authenticate_jwt(ctx: ApiContext, token: str) -> Principal:
    """Verify an IdP access token and resolve its linked member."""
    if ctx.verifier is None or ctx.identities is None:
        raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
    verified: VerifiedToken = await ctx.verifier.verify(token)
    async with ctx.sessions() as session:
        member: MemberAuth | None = await ctx.identities.member_for_identity(
            session, verified.issuer, verified.subject
        )
    if member is None:
        raise TelescreenError(ErrorCode.UNAUTHORIZED, "sign-in required")
    if not member.active or member.role is None:
        raise TelescreenError(ErrorCode.FORBIDDEN, "permission is not granted")
    return Principal(
        PrincipalKind.OIDC,
        None,
        member.member_id,
        member.role,
        role_permissions(member.role),
    )


async def _authenticate_api_token(ctx: ApiContext, token: str) -> Principal:
    """Resolve a hashed API token and its bound member role."""
    role: Role | None = None
    async with ctx.sessions() as session:
        row: ApiTokenRow | None = await session.scalar(
            select(ApiTokenRow).where(ApiTokenRow.token_hash == hash_token(token))
        )
        if row is None or row.revoked_at is not None:
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        if row.member_id is not None:
            member: MemberRow | None = await session.get(MemberRow, row.member_id)
            if member is not None and member.active:
                role = _stored_role(member.role)
    scopes: frozenset[ApiScope] = frozenset(
        ApiScope(value) for value in row.scopes.split(",") if value
    )
    bound: bool = row.member_id is not None
    return Principal(
        PrincipalKind.TOKEN,
        row.id,
        row.member_id,
        role,
        effective_token_permissions(scopes, role, bound),
    )


async def authenticate(ctx: ApiContext, request: Request) -> Principal:
    """Authenticate a bearer API token or IdP access token without exposing it."""
    authorization: str | None = request.headers.get("authorization")
    if authorization is None or not authorization.startswith("Bearer "):
        raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
    token: str = authorization[7:]
    if not token:
        raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
    if token.count(".") == 2:
        return await _authenticate_jwt(ctx, token)
    return await _authenticate_api_token(ctx, token)


@dataclass(frozen=True, eq=False)
class PermissionDependency:
    """Request dependency that authenticates and requires one permission.

    Identity equality keeps it hashable for FastAPI's dependency cache even when the
    context holds unhashable services.
    """

    ctx: ApiContext
    permission: Permission

    async def __call__(self, request: Request) -> Principal:
        """Return the authenticated principal or reject the request."""
        principal: Principal = await authenticate(self.ctx, request)
        if not principal.allows(self.permission):
            raise TelescreenError(ErrorCode.FORBIDDEN, "permission is not granted")
        return principal


def require_permission(ctx: ApiContext, permission: Permission) -> PermissionDependency:
    """Create the single authorization dependency for a route."""
    return PermissionDependency(ctx, permission)


@dataclass(frozen=True)
class ExternalAuthDeclaration:
    """No-op marker for routes authenticated outside bearer permissions."""

    kind: ExternalAuthKind

    async def __call__(self) -> None:
        """Declare the route's external authentication without checking anything."""
        return


def declare_external(kind: ExternalAuthKind) -> ExternalAuthDeclaration:
    """Create the marker dependency for an externally authenticated route."""
    return ExternalAuthDeclaration(kind)
