"""Database operations for members, teams, memberships and API tokens."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.db.models import ApiTokenRow, MemberRow, TeamMembershipRow, TeamRow


@dataclass
class DirectoryRepository:
    """CRUD operations over directory tables."""

    async def member_by_handle(self, session: AsyncSession, handle: str) -> MemberRow | None:
        """Find a member by its unique handle."""
        return await session.scalar(select(MemberRow).where(MemberRow.handle == handle))

    async def team_by_slug(self, session: AsyncSession, slug: str) -> TeamRow | None:
        """Find a team by its unique slug."""
        return await session.scalar(select(TeamRow).where(TeamRow.slug == slug))

    async def add_api_token(self, session: AsyncSession, row: ApiTokenRow) -> None:
        """Add a token row inside the caller's transaction."""
        session.add(row)

    async def api_token_by_hash(self, session: AsyncSession, token_hash: str) -> ApiTokenRow | None:
        """Find a token by its indexed SHA-256 digest."""
        return await session.scalar(select(ApiTokenRow).where(ApiTokenRow.token_hash == token_hash))

    async def api_token_by_id(self, session: AsyncSession, token_id: str) -> ApiTokenRow | None:
        """Find a token by its public identifier."""
        return await session.scalar(select(ApiTokenRow).where(ApiTokenRow.id == token_id))

    async def list_members(self, session: AsyncSession) -> Sequence[MemberRow]:
        """List members in stable handle order."""
        result: ScalarResult[MemberRow] = await session.scalars(
            select(MemberRow).order_by(MemberRow.handle)
        )
        return result.all()

    async def list_teams(self, session: AsyncSession) -> Sequence[TeamRow]:
        """List teams in stable slug order."""
        result: ScalarResult[TeamRow] = await session.scalars(
            select(TeamRow).order_by(TeamRow.slug)
        )
        return result.all()

    async def members_for_team(self, session: AsyncSession, team_id: str) -> Sequence[MemberRow]:
        """List team members in stable handle order."""
        result: ScalarResult[MemberRow] = await session.scalars(
            select(MemberRow)
            .join(TeamMembershipRow, TeamMembershipRow.member_id == MemberRow.id)
            .where(TeamMembershipRow.team_id == team_id)
            .order_by(MemberRow.handle)
        )
        return result.all()

    async def set_team_members(
        self, session: AsyncSession, team_id: str, member_ids: Sequence[str]
    ) -> None:
        """Replace the complete team membership set."""
        await session.execute(delete(TeamMembershipRow).where(TeamMembershipRow.team_id == team_id))
        session.add_all(
            TeamMembershipRow(team_id=team_id, member_id=member_id) for member_id in member_ids
        )
