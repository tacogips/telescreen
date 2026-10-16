"""Member reference helpers for personal notification delivery."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import MemberRow


class MemberReferences(Protocol):
    """Resolve member ids to the notification reference used by this store."""

    async def member_reference(self, session: AsyncSession, member_id: str) -> str:
        """Return a member reference or raise recipient_unknown."""
        ...


@dataclass(frozen=True)
class MainMemberReferences:
    """Resolve a member through the main-store members table."""

    async def member_reference(self, session: AsyncSession, member_id: str) -> str:
        """Return the member's handle-based notification reference."""
        return await member_reference(session, member_id)


async def member_reference(session: AsyncSession, member_id: str) -> str:
    """Resolve a member id to the handle-based notification reference."""
    member: MemberRow | None = await session.get(MemberRow, member_id)
    if member is None or not member.active:
        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "member recipient is unavailable")
    return f"member:{member.handle}"
