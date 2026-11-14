"""Persistence helpers for notification delivery rows."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.vocabulary import NotificationStatus
from telescreen.db.models.notifications import NotificationRow


class NotificationRepository:
    """Insert, load, and update notifications inside caller-owned sessions."""

    async def insert(self, session: AsyncSession, row: NotificationRow) -> None:
        """Add a notification row and flush its identifier in the caller transaction."""
        session.add(row)
        await session.flush()

    async def get(self, session: AsyncSession, notification_id: str) -> NotificationRow | None:
        """Load a notification by id."""
        row: NotificationRow | None = await session.get(NotificationRow, notification_id)
        if row is not None:
            row.created_at = ensure_utc(row.created_at)
            row.sent_at = ensure_utc(row.sent_at) if row.sent_at is not None else None
        return row

    async def set_delivery(
        self,
        session: AsyncSession,
        row: NotificationRow,
        status: NotificationStatus,
        attempts: int,
        sent_at: datetime | None,
    ) -> None:
        """Update delivery state and normalize a successful timestamp to UTC."""
        row.status = status.value
        row.delivery_attempts = attempts
        row.sent_at = ensure_utc(sent_at) if sent_at is not None else None
        await session.flush()
