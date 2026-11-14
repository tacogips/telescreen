"""Bearer-token authentication and API scope dependencies."""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select

from telescreen.api.common.context import ApiContext
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ApiScope, ErrorCode
from telescreen.db.models import ApiTokenRow


@dataclass(frozen=True)
class Principal:
    """Authenticated API identity and its allowed scopes."""

    token_id: str
    member_id: str | None
    scopes: frozenset[ApiScope]

    def has(self, scope: ApiScope) -> bool:
        """Return whether this principal holds a scope or admin access."""
        return ApiScope.ADMIN in self.scopes or scope in self.scopes

    def actor_ref(self) -> str:
        """Return a stable, non-secret audit reference."""
        if self.member_id is not None:
            return f"member:{self.member_id}"
        return f"token:{self.token_id}"


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest used for indexed token lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_scope(ctx: ApiContext, scope: ApiScope) -> Callable[[Request], Awaitable[Principal]]:
    """Create a request dependency that authenticates and authorizes a bearer token."""

    async def dependency(request: Request) -> Principal:
        """Resolve the bearer token without exposing its value."""
        authorization: str | None = request.headers.get("authorization")
        if authorization is None or not authorization.startswith("Bearer "):
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        token: str = authorization[7:]
        if not token:
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        async with ctx.sessions() as session:
            row: ApiTokenRow | None = await session.scalar(
                select(ApiTokenRow).where(ApiTokenRow.token_hash == hash_token(token))
            )
        if row is None or row.revoked_at is not None:
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        scopes: frozenset[ApiScope] = frozenset(
            ApiScope(value) for value in row.scopes.split(",") if value
        )
        principal: Principal = Principal(row.id, row.member_id, scopes)
        if not principal.has(scope):
            raise TelescreenError(ErrorCode.FORBIDDEN, "scope is not permitted")
        return principal

    return dependency
