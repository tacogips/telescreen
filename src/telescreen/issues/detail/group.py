"""Merged issue group query helpers."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import IssueRow


async def group_issue_ids(session: AsyncSession, issue_id: str) -> tuple[str, ...]:
    """Return a primary issue and its active merged children."""
    issue: IssueRow | None = await session.scalar(select(IssueRow).where(IssueRow.id == issue_id))
    if issue is None:
        raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
    primary_id: str = issue.merged_into_id or issue.id
    children: tuple[str, ...] = tuple(
        await session.scalars(
            select(IssueRow.id)
            .where(IssueRow.merged_into_id == primary_id, IssueRow.status != "merged")
            .order_by(IssueRow.id)
        )
    )
    return (primary_id, *children)
