"""Bearer authentication against a pushed directory replica."""

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.api.common.auth import hash_token
from telescreen.core.errors import TelescreenError
from telescreen.core.permissions import effective_token_permissions, role_permissions
from telescreen.core.vocabulary import ApiScope, ErrorCode, IdentityKind, PrincipalKind, Role
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.ports.collectors import OwnerIdentity
from telescreen.ports.directory_projection import (
    ApiTokenEntity,
    DirectoryEntity,
    DirectoryEntityKind,
    IdentityEntity,
    MemberEntity,
)
from telescreen.ports.identity import BearerVerifier, VerifiedToken
from telescreen.ports.principal import Principal
from telescreen.replica.store import DirectoryReplica


@dataclass(frozen=True)
class ReplicaAuthenticator:
    """Authenticate bearer tokens with local replica data and matching API errors."""

    sessions: async_sessionmaker[AsyncSession]
    replica: DirectoryReplica
    verifier: BearerVerifier | None
    fingerprinter: Fingerprinter | None

    async def __call__(self, request: Request) -> Principal:
        """Authenticate a bearer from request headers without exposing it."""
        authorization: str | None = request.headers.get("authorization")
        if authorization is None or not authorization.startswith("Bearer "):
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        token: str = authorization[7:]
        if not token:
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        return await self._oidc(token) if token.count(".") == 2 else await self._api_token(token)

    async def _oidc(self, token: str) -> Principal:
        if self.verifier is None or self.fingerprinter is None:
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        verified: VerifiedToken = await self.verifier.verify(token)
        owner: str = self.fingerprinter.owner_ref(
            OwnerIdentity(IdentityKind.OIDC_SUBJECT, verified.issuer + "\0" + verified.subject)
        )
        identity_key: str = owner.partition(":")[2]
        async with self.sessions() as session:
            identity: DirectoryEntity | None = await self.replica.by_lookup(
                session,
                DirectoryEntityKind.IDENTITY,
                f"{IdentityKind.OIDC_SUBJECT.value}:{identity_key}",
            )
            if identity is None or not isinstance(identity.payload, IdentityEntity):
                raise TelescreenError(ErrorCode.UNAUTHORIZED, "sign-in required")
            member_entity: DirectoryEntity | None = await self.replica.get(
                session, DirectoryEntityKind.MEMBER, identity.payload.member_id
            )
        member: MemberEntity | None = (
            member_entity.payload
            if member_entity is not None and isinstance(member_entity.payload, MemberEntity)
            else None
        )
        role: Role | None = _role(member.role) if member is not None else None
        if member is None:
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "sign-in required")
        if not member.active or role is None:
            raise TelescreenError(ErrorCode.FORBIDDEN, "permission is not granted")
        return Principal(PrincipalKind.OIDC, None, member.id, role, role_permissions(role))

    async def _api_token(self, token: str) -> Principal:
        async with self.sessions() as session:
            entity: DirectoryEntity | None = await self.replica.by_lookup(
                session, DirectoryEntityKind.API_TOKEN, hash_token(token)
            )
            if (
                entity is None
                or not isinstance(entity.payload, ApiTokenEntity)
                or entity.payload.revoked
            ):
                raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
            token_value: ApiTokenEntity = entity.payload
            member: MemberEntity | None = None
            if token_value.member_id is not None:
                member_entity: DirectoryEntity | None = await self.replica.get(
                    session, DirectoryEntityKind.MEMBER, token_value.member_id
                )
                if member_entity is not None and isinstance(member_entity.payload, MemberEntity):
                    member = member_entity.payload
        role_value: Role | None = (
            _role(member.role) if member is not None and member.active else None
        )
        scopes: frozenset[ApiScope] = frozenset(ApiScope(scope) for scope in token_value.scopes)
        bound: bool = token_value.member_id is not None
        return Principal(
            PrincipalKind.TOKEN,
            token_value.id,
            token_value.member_id,
            role_value,
            effective_token_permissions(scopes, role_value, bound),
        )


def _role(value: str | None) -> Role | None:
    """Parse a stored role, treating unknown values as unassigned."""
    if value is None:
        return None
    try:
        return Role(value)
    except ValueError:
        return None
