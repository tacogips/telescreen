"""Member reference helpers for personal notification delivery."""

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import MemberRow


async def member_reference(session: AsyncSession, member_id: str) -> str:
    """Resolve a member id to the handle-based notification reference."""
    member: MemberRow | None = await session.get(MemberRow, member_id)
    if member is None or not member.active:
        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "member recipient is unavailable")
    return f"member:{member.handle}"
