"""Directory use cases and audit events."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import ActorKind, ErrorCode
from telescreen.db.models import MemberRow, TeamRow
from telescreen.directory.identities import IdentityService
from telescreen.directory.repository import DirectoryRepository
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.ports.audit import AuditEntry, AuditLog


@dataclass
class DirectoryService:
    """Create and query Reporting Bureau directory records."""

    audit: AuditLog
    clock: Clock
    repository: DirectoryRepository = field(default_factory=DirectoryRepository)
    fingerprinter: Fingerprinter | None = None

    async def create_member(
        self,
        session: AsyncSession,
        handle: str,
        display_name: str,
        email: str | None,
        slack_user_id: str | None,
    ) -> MemberRow:
        """Create a member and append an audit record in the caller transaction."""
        if await self.repository.member_by_handle(session, handle) is not None:
            raise TelescreenError(ErrorCode.CONFLICT, "member handle already exists")
        now: datetime = self.clock.now()
        member: MemberRow = MemberRow(
            id=new_id(),
            handle=handle,
            display_name=display_name,
            email=email,
            slack_user_id=slack_user_id,
            active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(member)
        if self.fingerprinter is not None:
            identities: IdentityService = IdentityService(
                self.fingerprinter, self.audit, self.clock
            )
            await identities.derive_for_member(session, member)
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind=ActorKind.SYSTEM,
                actor_ref="system:directory",
                action="directory.member_created",
                target_kind="member",
                target_id=member.id,
                summary="Directory member created",
                details=json_object((("handle", json_str(handle)),)),
            ),
        )
        return member

    async def create_team(
        self,
        session: AsyncSession,
        slug: str,
        name: str,
        email: str | None,
        slack_channel: str | None,
        lead_handle: str | None = None,
    ) -> TeamRow:
        """Create a team and append an audit record in the caller transaction."""
        if await self.repository.team_by_slug(session, slug) is not None:
            raise TelescreenError(ErrorCode.CONFLICT, "team slug already exists")
        lead: MemberRow | None = (
            await self.repository.member_by_handle(session, lead_handle)
            if lead_handle is not None
            else None
        )
        if lead_handle is not None and (lead is None or not lead.active):
            raise TelescreenError(ErrorCode.NOT_FOUND, "team lead not found")
        now: datetime = self.clock.now()
        team: TeamRow = TeamRow(
            id=new_id(),
            slug=slug,
            name=name,
            email=email,
            slack_channel=slack_channel,
            created_at=now,
            updated_at=now,
            lead_member_id=lead.id if lead is not None else None,
        )
        session.add(team)
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind=ActorKind.SYSTEM,
                actor_ref="system:directory",
                action="directory.team_created",
                target_kind="team",
                target_id=team.id,
                summary="Directory team created",
                details=json_object((("slug", json_str(slug)),)),
            ),
        )
        return team

    async def set_team_lead(
        self, session: AsyncSession, slug: str, lead_handle: str | None, actor_ref: str
    ) -> None:
        """Set or clear a team's active lead and append an audit record."""
        team: TeamRow | None = await self.repository.team_by_slug(session, slug)
        if team is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "team not found")
        lead: MemberRow | None = (
            await self.repository.member_by_handle(session, lead_handle)
            if lead_handle is not None
            else None
        )
        if lead_handle is not None and (lead is None or not lead.active):
            raise TelescreenError(ErrorCode.NOT_FOUND, "team lead not found")
        team.lead_member_id = lead.id if lead is not None else None
        team.updated_at = self.clock.now()
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind=ActorKind.MEMBER,
                actor_ref=actor_ref,
                action="team.lead_changed",
                target_kind="team",
                target_id=team.id,
                summary="Team lead changed",
                details=json_object(
                    (("lead_set", json_str("true" if lead is not None else "false")),)
                ),
            ),
        )

    async def set_team_members(
        self, session: AsyncSession, slug: str, handles: Sequence[str]
    ) -> None:
        """Replace team membership after validating every member handle."""
        team: TeamRow | None = await self.repository.team_by_slug(session, slug)
        if team is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "team not found")
        members: list[MemberRow] = []
        for handle in handles:
            member: MemberRow | None = await self.repository.member_by_handle(session, handle)
            if member is None or not member.active:
                raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")
            members.append(member)
        await self.repository.set_team_members(
            session, team.id, tuple(member.id for member in members)
        )
        team.updated_at = self.clock.now()
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind=ActorKind.SYSTEM,
                actor_ref="system:directory",
                action="directory.team_members_set",
                target_kind="team",
                target_id=team.id,
                summary="Directory team membership updated",
                details=json_object((("member_count", json_str(str(len(members)))),)),
            ),
        )

    async def list_members(self, session: AsyncSession) -> Sequence[MemberRow]:
        """List members."""
        return await self.repository.list_members(session)

    async def list_teams(self, session: AsyncSession) -> Sequence[TeamRow]:
        """List teams."""
        return await self.repository.list_teams(session)
