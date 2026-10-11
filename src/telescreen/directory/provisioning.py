"""OIDC member provisioning: keyed identity lookup, linking, creation and sync."""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_int, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    ErrorCode,
    IdentityKind,
    IdentityOrigin,
    MembershipOrigin,
    OidcProvisioning,
    RoleChangeSource,
    SignInProvisioning,
)
from telescreen.db.models import MemberIdentityRow, MemberRow, TeamMembershipRow, TeamRow
from telescreen.directory.identities import IdentityService
from telescreen.directory.repository import DirectoryRepository
from telescreen.directory.roles import RoleService, stored_role
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.identity import MemberAuth, MemberProvisioner, SignInRequest, SignInResult

logger: logging.Logger = logging.getLogger(__name__)

SIGN_IN_ACTOR: str = "system:auth"
HANDLE_MAX_LENGTH: int = 64
_HANDLE_INVALID: re.Pattern[str] = re.compile(r"[^a-z0-9-]+")
_HANDLE_DASHES: re.Pattern[str] = re.compile(r"-{2,}")


def handle_base(source: str) -> str:
    """Slugify a claim into a member handle base: lowercase, [a-z0-9-], at most 64 chars."""
    slug: str = _HANDLE_INVALID.sub("-", source.strip().lower())
    slug = _HANDLE_DASHES.sub("-", slug).strip("-")[:HANDLE_MAX_LENGTH].strip("-")
    return slug or "user"


def _member_auth(member: MemberRow) -> MemberAuth:
    """Project a member row to its safe sign-in identity."""
    return MemberAuth(
        member_id=member.id,
        handle=member.handle,
        display_name=member.display_name,
        role=stored_role(member.role),
        active=member.active,
    )


@dataclass
class DirectoryProvisioner(MemberProvisioner):
    """Resolve, link or create members for verified OIDC identities."""

    fingerprinter: Fingerprinter
    roles: RoleService
    audit: AuditLog
    clock: Clock
    repository: DirectoryRepository = field(default_factory=DirectoryRepository)

    def _identity_key(self, issuer: str, subject: str) -> tuple[str, str]:
        """Return the keyed (kind, digest) for an issuer and subject pair."""
        return IdentityService._key_parts(
            self.fingerprinter, IdentityKind.OIDC_SUBJECT, issuer + "\0" + subject
        )

    async def member_for_identity(
        self, session: AsyncSession, issuer: str, subject: str
    ) -> MemberAuth | None:
        """Return the member already linked to an issuer and subject."""
        kind: str
        key: str
        kind, key = self._identity_key(issuer, subject)
        identity: MemberIdentityRow | None = await self.repository.identity_by_key(
            session, kind, key
        )
        if identity is None:
            return None
        member: MemberRow | None = await self.repository.member_by_id(session, identity.member_id)
        return _member_auth(member) if member is not None else None

    async def sign_in(self, session: AsyncSession, request: SignInRequest) -> SignInResult:
        """Resolve, link or create a member, then sync its role, teams and sign-in time."""
        kind: str
        key: str
        kind, key = self._identity_key(request.issuer, request.subject)
        identity: MemberIdentityRow | None = await self.repository.identity_by_key(
            session, kind, key
        )
        member: MemberRow | None
        provisioning: SignInProvisioning
        if identity is not None:
            member = await self.repository.member_by_id(session, identity.member_id)
            if member is None:
                raise TelescreenError(ErrorCode.NOT_PROVISIONED, "member is not provisioned")
            provisioning = SignInProvisioning.EXISTING
        else:
            member, provisioning = await self._link_or_create(session, request)
            await self._link_identity(session, member, kind, key)
        if not member.active:
            raise TelescreenError(ErrorCode.FORBIDDEN, "member is inactive")
        role_changed: bool = stored_role(member.role) is not request.role
        if role_changed:
            await self.roles.set_role(
                session, member.handle, request.role, SIGN_IN_ACTOR, RoleChangeSource.IDP
            )
        if request.team_map:
            await self._sync_teams(session, member, request)
        member.last_sign_in_at = self.clock.now()
        return SignInResult(_member_auth(member), provisioning, role_changed)

    async def _link_or_create(
        self, session: AsyncSession, request: SignInRequest
    ) -> tuple[MemberRow, SignInProvisioning]:
        """Link the single verified-email match or create a member when allowed."""
        matches: Sequence[MemberRow] = ()
        if request.email_verified and request.email:
            matches = await self.repository.active_members_by_email(session, request.email)
        if len(matches) == 1:
            member: MemberRow = matches[0]
            linked: MemberIdentityRow | None = await self.repository.identity_of_kind(
                session, member.id, IdentityKind.OIDC_SUBJECT.value
            )
            if linked is not None:
                raise TelescreenError(
                    ErrorCode.IDENTITY_CONFLICT, "member is linked to another identity"
                )
            return member, SignInProvisioning.LINKED
        if not matches and request.provisioning is OidcProvisioning.CREATE:
            return await self._create_member(session, request), SignInProvisioning.CREATED
        raise TelescreenError(ErrorCode.NOT_PROVISIONED, "member is not provisioned")

    async def _create_member(self, session: AsyncSession, request: SignInRequest) -> MemberRow:
        """Create an active member without a role; role sync assigns it afterwards."""
        source: str = request.preferred_username or ""
        if not source.strip() and request.email:
            source = request.email.partition("@")[0]
        handle: str = await self._free_handle(session, handle_base(source))
        now: datetime = self.clock.now()
        member: MemberRow = MemberRow(
            id=new_id(),
            handle=handle,
            display_name=(request.display_name or "").strip() or handle,
            email=request.email if request.email_verified and request.email else None,
            slack_user_id=None,
            active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(member)
        await IdentityService(self.fingerprinter, self.audit, self.clock).derive_for_member(
            session, member
        )
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind=ActorKind.SYSTEM,
                actor_ref=SIGN_IN_ACTOR,
                action="member.provisioned",
                target_kind="member",
                target_id=member.id,
                summary="Member provisioned by sign-in",
                details=json_object((("method", json_str("oidc")),)),
            ),
        )
        return member

    async def _free_handle(self, session: AsyncSession, base: str) -> str:
        """Return the base handle or the first free -2, -3, ... suffixed variant."""
        candidate: str = base
        suffix_number: int = 2
        while await self.repository.member_by_handle(session, candidate) is not None:
            suffix: str = f"-{suffix_number}"
            candidate = base[: HANDLE_MAX_LENGTH - len(suffix)].rstrip("-") + suffix
            suffix_number += 1
        return candidate

    async def _link_identity(
        self, session: AsyncSession, member: MemberRow, kind: str, key: str
    ) -> None:
        """Insert the keyed OIDC identity row and audit the link by kind only."""
        row: MemberIdentityRow = MemberIdentityRow(
            id=new_id(),
            member_id=member.id,
            kind=kind,
            identity_key=key,
            origin=IdentityOrigin.OIDC.value,
            created_at=self.clock.now(),
        )
        session.add(row)
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind=ActorKind.SYSTEM,
                actor_ref=SIGN_IN_ACTOR,
                action="identity.linked",
                target_kind="member_identity",
                target_id=row.id,
                summary="Member identity linked",
                details=json_object((("kind", json_str(kind)),)),
            ),
        )

    async def _sync_teams(
        self, session: AsyncSession, member: MemberRow, request: SignInRequest
    ) -> None:
        """Add and remove idp-origin memberships of mapped teams; never touch manual rows."""
        mapped: list[str] = []
        targets: list[str] = []
        for mapping in request.team_map:
            team: TeamRow | None = await self.repository.team_by_slug(session, mapping.team_slug)
            if team is None:
                logger.warning("oidc team mapping references an unknown team")
                continue
            if team.id not in mapped:
                mapped.append(team.id)
            if mapping.group in request.groups and team.id not in targets:
                targets.append(team.id)
        added: int = 0
        removed: int = 0
        for team_id in mapped:
            existing: TeamMembershipRow | None = await self.repository.membership(
                session, team_id, member.id
            )
            if team_id in targets and existing is None:
                session.add(
                    TeamMembershipRow(
                        team_id=team_id, member_id=member.id, origin=MembershipOrigin.IDP.value
                    )
                )
                added += 1
            elif (
                team_id not in targets
                and existing is not None
                and existing.origin == MembershipOrigin.IDP.value
            ):
                await session.delete(existing)
                removed += 1
        if added or removed:
            await self.audit.append(
                session,
                AuditEntry(
                    actor_kind=ActorKind.SYSTEM,
                    actor_ref=SIGN_IN_ACTOR,
                    action="team_membership.synced",
                    target_kind="member",
                    target_id=member.id,
                    summary="Team memberships synced from sign-in",
                    details=json_object(
                        (("added", json_int(added)), ("removed", json_int(removed)))
                    ),
                ),
            )
