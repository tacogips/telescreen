"""Resolve keyed owners to active directory references."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import IdentityKind
from telescreen.db.models import MemberIdentityRow, MemberRow, TeamMembershipRow, TeamRow
from telescreen.directory.repository import DirectoryRepository
from telescreen.ports.directory import OwnerDirectory


@dataclass
class DirectoryOwnerResolver(OwnerDirectory):
    """Resolve owner identities, members, team memberships and team leads."""

    repository: DirectoryRepository = field(default_factory=DirectoryRepository)

    async def member_ref(self, session: AsyncSession, member_id: str) -> str | None:
        """Return a handle-based reference for an active member."""
        member: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.id == member_id, MemberRow.active.is_(True))
        )
        return f"member:{member.handle}" if member is not None else None

    async def team_ref(self, session: AsyncSession, team_id: str) -> str | None:
        """Return a slug-based reference for a configured team."""
        team: TeamRow | None = await session.scalar(select(TeamRow).where(TeamRow.id == team_id))
        return f"team:{team.slug}" if team is not None else None

    async def team_refs_of(self, session: AsyncSession, member_id: str) -> tuple[str, ...]:
        """Return all configured team references for an active member."""
        member: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.id == member_id, MemberRow.active.is_(True))
        )
        if member is None:
            return ()
        result: ScalarResult[str] = await session.scalars(
            select(TeamRow.slug)
            .join(TeamMembershipRow, TeamMembershipRow.team_id == TeamRow.id)
            .where(TeamMembershipRow.member_id == member_id)
            .order_by(TeamRow.slug)
        )
        return tuple(f"team:{slug}" for slug in result.all())

    async def member_for_owner(self, session: AsyncSession, owner_ref: str) -> str | None:
        """Resolve a well-formed keyed identity to its active member reference."""
        kind: str
        separator: str
        key: str
        kind, separator, key = owner_ref.partition(":")
        if (
            not separator
            or len(key) != 64
            or any(character not in "0123456789abcdef" for character in key)
            or kind not in tuple(item.value for item in IdentityKind)
        ):
            return None
        member: MemberRow | None = await session.scalar(
            select(MemberRow)
            .join(MemberIdentityRow, MemberIdentityRow.member_id == MemberRow.id)
            .where(
                MemberIdentityRow.kind == kind,
                MemberIdentityRow.identity_key == key,
                MemberRow.active.is_(True),
            )
        )
        return f"member:{member.handle}" if member is not None else None

    async def team_lead(self, session: AsyncSession, team_slug: str) -> str | None:
        """Return a team's active lead as a handle-based member reference."""
        member: MemberRow | None = await session.scalar(
            select(MemberRow)
            .join(TeamRow, TeamRow.lead_member_id == MemberRow.id)
            .where(
                TeamRow.slug == team_slug,
                MemberRow.active.is_(True),
            )
        )
        return f"member:{member.handle}" if member is not None else None
