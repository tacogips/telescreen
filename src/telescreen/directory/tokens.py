"""One-time API token creation and revocation."""

import base64
import secrets
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common.auth import hash_token
from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import ActorKind, ApiScope, ErrorCode
from telescreen.db.models import ApiTokenRow
from telescreen.directory.repository import DirectoryRepository
from telescreen.ports.audit import AuditEntry, AuditLog


@dataclass(frozen=True)
class CreatedToken:
    """A newly minted token that is returned only once."""

    token_id: str
    token: str = field(repr=False)


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
                actor_kind=ActorKind.SYSTEM,
                actor_ref="system:tokens",
                action="token.created",
                target_kind="api_token",
                target_id=token_id,
                summary="API token created",
                details=json_object((("label", json_str(label)),)),
            ),
        )
        return CreatedToken(token_id=token_id, token=raw_token)

    async def revoke(self, session: AsyncSession, token_id: str) -> None:
        """Revoke an active token and append an audit record."""
        row: ApiTokenRow | None = await self.repository.api_token_by_id(session, token_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "token not found")
        if row.revoked_at is None:
            row.revoked_at = self.clock.now()
            await self.audit.append(
                session,
                AuditEntry(
                    actor_kind=ActorKind.SYSTEM,
                    actor_ref="system:tokens",
                    action="token.revoked",
                    target_kind="api_token",
                    target_id=row.id,
                    summary="API token revoked",
                    details=json_object(()),
                ),
            )
