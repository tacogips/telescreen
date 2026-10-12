"""Chronological finding selection for issue detail navigation."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import FindingRow


@dataclass(frozen=True)
class EventSelection:
    """Selected finding and its chronological neighbors."""

    event: FindingRow
    previous_id: str | None
    next_id: str | None
    position: int
    total: int


async def select_event(
    session: AsyncSession, issue_ids: tuple[str, ...], selector: str
) -> EventSelection:
    """Select an event with previous meaning older and next meaning newer (B1)."""
    rows: tuple[FindingRow, ...] = tuple(
        await session.scalars(
            select(FindingRow)
            .where(FindingRow.issue_id.in_(issue_ids))
            .order_by(FindingRow.observed_at, FindingRow.id)
        )
    )
    if not rows:
        raise TelescreenError(ErrorCode.NOT_FOUND, "event not found")
    event: FindingRow | None = None
    if selector == "oldest":
        event = rows[0]
    elif selector == "latest":
        event = rows[-1]
    elif selector == "recommended":
        event = max(rows, key=lambda row: (row.confidence, row.observed_at, row.id))
    else:
        event = next((row for row in rows if row.id == selector), None)
    if event is None:
        raise TelescreenError(ErrorCode.NOT_FOUND, "event not found")
    position: int = next(index for index, row in enumerate(rows) if row.id == event.id)
    return EventSelection(
        event,
        rows[position - 1].id if position > 0 else None,
        rows[position + 1].id if position + 1 < len(rows) else None,
        position + 1,
        len(rows),
    )
