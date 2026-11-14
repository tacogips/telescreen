"""Database operations for members, teams, memberships and API tokens."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Row, ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import MembershipOrigin, Role
from telescreen.db.models import (
    ApiTokenRow,
    MemberIdentityRow,
    MemberRow,
    TeamMembershipRow,
    TeamRow,
)


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
            TeamMembershipRow(
                team_id=team_id, member_id=member_id, origin=MembershipOrigin.MANUAL.value
            )
            for member_id in member_ids
        )

    async def member_by_id(self, session: AsyncSession, member_id: str) -> MemberRow | None:
        """Find a member by its identifier."""
        return await session.scalar(select(MemberRow).where(MemberRow.id == member_id))

    async def identity_by_key(
        self, session: AsyncSession, kind: str, identity_key: str
    ) -> MemberIdentityRow | None:
        """Find a keyed identity row by kind and digest."""
        return await session.scalar(
            select(MemberIdentityRow).where(
                MemberIdentityRow.kind == kind,
                MemberIdentityRow.identity_key == identity_key,
            )
        )

    async def identity_of_kind(
        self, session: AsyncSession, member_id: str, kind: str
    ) -> MemberIdentityRow | None:
        """Find any identity row of one kind for a member."""
        return await session.scalar(
            select(MemberIdentityRow)
            .where(MemberIdentityRow.member_id == member_id, MemberIdentityRow.kind == kind)
            .limit(1)
        )

    async def active_members_by_email(
        self, session: AsyncSession, email: str
    ) -> Sequence[MemberRow]:
        """List active members whose email matches case-insensitively."""
        result: ScalarResult[MemberRow] = await session.scalars(
            select(MemberRow)
            .where(func.lower(MemberRow.email) == email.lower(), MemberRow.active.is_(True))
            .order_by(MemberRow.handle)
        )
        return result.all()

    async def active_admins_for_update(self, session: AsyncSession) -> Sequence[MemberRow]:
        """Lock and list every active admin member."""
        result: ScalarResult[MemberRow] = await session.scalars(
            select(MemberRow)
            .where(MemberRow.role == Role.ADMIN.value, MemberRow.active.is_(True))
            .with_for_update()
        )
        return result.all()

    async def member_ids_with_identity_kind(self, session: AsyncSession, kind: str) -> set[str]:
        """Return the identifiers of members holding at least one identity of a kind."""
        result: ScalarResult[str] = await session.scalars(
            select(MemberIdentityRow.member_id).where(MemberIdentityRow.kind == kind).distinct()
        )
        return set(result.all())

    async def team_slugs_by_member(self, session: AsyncSession) -> Sequence[Row[tuple[str, str]]]:
        """List (member id, team slug) pairs in stable slug order."""
        return (
            await session.execute(
                select(TeamMembershipRow.member_id, TeamRow.slug)
                .join(TeamRow, TeamRow.id == TeamMembershipRow.team_id)
                .order_by(TeamRow.slug)
            )
        ).all()

    async def membership(
        self, session: AsyncSession, team_id: str, member_id: str
    ) -> TeamMembershipRow | None:
        """Find one membership row of any origin."""
        return await session.scalar(
            select(TeamMembershipRow).where(
                TeamMembershipRow.team_id == team_id,
                TeamMembershipRow.member_id == member_id,
            )
        )

    async def list_api_tokens(self, session: AsyncSession) -> Sequence[ApiTokenRow]:
        """List tokens in creation order."""
        result: ScalarResult[ApiTokenRow] = await session.scalars(
            select(ApiTokenRow).order_by(ApiTokenRow.created_at, ApiTokenRow.id)
        )
        return result.all()
