"""Persistence helpers for member issue bookmarks."""

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.db.models import IssueBookmarkRow


async def bookmark(
    session: AsyncSession, issue_id: str, member_id: str, clock: Clock
) -> IssueBookmarkRow:
    """Create a bookmark when it does not already exist."""
    row: IssueBookmarkRow | None = await session.get(IssueBookmarkRow, (issue_id, member_id))
    if row is None:
        row = IssueBookmarkRow(issue_id=issue_id, member_id=member_id, created_at=clock.now())
        session.add(row)
    return row


async def unbookmark(session: AsyncSession, issue_id: str, member_id: str) -> None:
    """Remove a member's bookmark when present."""
    row: IssueBookmarkRow | None = await session.get(IssueBookmarkRow, (issue_id, member_id))
    if row is not None:
        await session.delete(row)
