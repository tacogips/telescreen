"""One-time API token creation and revocation."""

import base64
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common.auth import hash_token
from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import ApiScope, ErrorCode, Role
from telescreen.db.models import ApiTokenRow, MemberRow
from telescreen.directory.actors import actor_kind_for
from telescreen.directory.repository import DirectoryRepository
from telescreen.ports.audit import AuditEntry, AuditLog


@dataclass(frozen=True)
class CreatedToken:
    """A newly minted token that is returned only once."""

    token_id: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class TokenView:
    """Token metadata safe for administrative listing; never the token or its hash."""

    id: str
    label: str
    scopes: tuple[str, ...]
    member_handle: str | None
    created_at: datetime
    revoked_at: datetime | None


def _handle_of(members: Sequence[MemberRow], member_id: str | None) -> str | None:
    """Return the handle of a bound member, or None for an unbound or unknown member."""
    if member_id is None:
        return None
    return next((member.handle for member in members if member.id == member_id), None)


@dataclass
class TokenService:
    """Issue and revoke hashed bearer tokens."""

    audit: AuditLog
    clock: Clock
    repository: DirectoryRepository = field(default_factory=DirectoryRepository)

    async def create(
        self,
        session: AsyncSession,
        label: str,
        scopes: frozenset[ApiScope],
        member_id: str | None,
        actor_ref: str = "system:tokens",
    ) -> CreatedToken:
        """Create a token, persisting and auditing only its digest and metadata."""
        raw_token: str = (
            base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
        )
        token_id: str = new_id()
        await self.repository.add_api_token(
            session,
            ApiTokenRow(
                id=token_id,
                label=label,
                token_hash=hash_token(raw_token),
                scopes=",".join(sorted(scope.value for scope in scopes)),
                member_id=member_id,
                created_at=self.clock.now(),
                revoked_at=None,
            ),
        )
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind=actor_kind_for(actor_ref),
                actor_ref=actor_ref,
                action="token.created",
                target_kind="api_token",
                target_id=token_id,
                summary="API token created",
                details=json_object((("label", json_str(label)),)),
            ),
        )
        return CreatedToken(token_id=token_id, token=raw_token)

    async def revoke(
        self, session: AsyncSession, token_id: str, actor_ref: str = "system:tokens"
    ) -> None:
        """Revoke an active token and append an audit record."""
        row: ApiTokenRow | None = await self.repository.api_token_by_id(session, token_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "token not found")
        if row.revoked_at is None:
            row.revoked_at = self.clock.now()
            await self.audit.append(
                session,
                AuditEntry(
                    actor_kind=actor_kind_for(actor_ref),
                    actor_ref=actor_ref,
                    action="token.revoked",
                    target_kind="api_token",
                    target_id=row.id,
                    summary="API token revoked",
                    details=json_object(()),
                ),
            )

    async def list_tokens(self, session: AsyncSession) -> tuple[TokenView, ...]:
        """List token metadata in creation order with the bound member handle."""
        tokens: Sequence[ApiTokenRow] = await self.repository.list_api_tokens(session)
        members: Sequence[MemberRow] = await self.repository.list_members(session)
        return tuple(
            TokenView(
                id=row.id,
                label=row.label,
                scopes=tuple(scope for scope in row.scopes.split(",") if scope),
                member_handle=_handle_of(members, row.member_id),
                created_at=row.created_at,
                revoked_at=row.revoked_at,
            )
            for row in tokens
        )

    async def create_for_member(
        self,
        session: AsyncSession,
        member_id: str,
        role: Role,
        label: str,
        scopes: frozenset[ApiScope],
        caller_token_id: str | None = None,
    ) -> CreatedToken:
        """Create a caller-bound token restricted to the caller's effective scopes."""
        allowed: frozenset[ApiScope] = {
            Role.VIEWER: frozenset((ApiScope.READ,)),
            Role.RESPONDER: frozenset((ApiScope.READ, ApiScope.WRITE)),
            Role.ADMIN: frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN)),
        }[role]
        if caller_token_id is not None:
            caller: ApiTokenRow | None = await self.repository.api_token_by_id(
                session, caller_token_id
            )
            if caller is None or caller.revoked_at is not None or caller.member_id != member_id:
                raise TelescreenError(ErrorCode.FORBIDDEN, "caller token is not active")
            caller_scopes: frozenset[ApiScope] = frozenset(
                ApiScope(scope) for scope in caller.scopes.split(",") if scope
            )
            allowed = allowed.intersection(caller_scopes)
        if not scopes or not scopes.issubset(allowed):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "token scopes exceed member role")
        return await self.create(session, label, scopes, member_id, f"member:{member_id}")

    async def list_for_member(self, session: AsyncSession, member_id: str) -> tuple[TokenView, ...]:
        """List only the caller's own token metadata."""
        tokens: Sequence[ApiTokenRow] = await self.repository.list_api_tokens(session)
        return tuple(
            TokenView(
                id=row.id,
                label=row.label,
                scopes=tuple(scope for scope in row.scopes.split(",") if scope),
                member_handle=None,
                created_at=row.created_at,
                revoked_at=row.revoked_at,
            )
            for row in tokens
            if row.member_id == member_id
        )

    async def revoke_for_member(self, session: AsyncSession, member_id: str, token_id: str) -> None:
        """Revoke only a token owned by the caller, hiding other token ids."""
        row: ApiTokenRow | None = await self.repository.api_token_by_id(session, token_id)
        if row is None or row.member_id != member_id:
            raise TelescreenError(ErrorCode.NOT_FOUND, "token not found")
        await self.revoke(session, token_id, f"member:{member_id}")

    async def member_id_for_handle(self, session: AsyncSession, handle: str) -> str:
        """Resolve a member handle for token binding."""
        member: MemberRow | None = await self.repository.member_by_handle(session, handle)
        if member is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "member not found")
        return member.id
