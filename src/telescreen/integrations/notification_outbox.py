"""Producer-local durable notification command outbox."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.db.models.notification_handoff import NotificationOutboxRow
from telescreen.ports.notification_commands import (
    NotificationCommand,
    decode_notification_command,
    encode_notification_command,
)


@dataclass(frozen=True)
class NotificationOutbox:
    """Persist immutable notification commands in the producer transaction."""

    async def append(self, session: AsyncSession, command: NotificationCommand) -> None:
        """Store the strictly encoded command under its durable event identity."""
        session.add(
            NotificationOutboxRow(
                id=command.event_id,
                payload_json=encode_notification_command(command),
                recorded_at=command.occurred_at,
            )
        )
        await session.flush()

    async def pending_ids(
        self, session: AsyncSession, limit: int, after_id: str | None = None
    ) -> tuple[str, ...]:
        """Page unacknowledged event ids in stable order."""
        if limit <= 0:
            return ()
        return tuple(
            (
                await session.scalars(
                    select(NotificationOutboxRow.id)
                    .where(
                        NotificationOutboxRow.delivered_at.is_(None),
                        NotificationOutboxRow.id > (after_id or ""),
                    )
                    .order_by(NotificationOutboxRow.id)
                    .limit(limit)
                )
            ).all()
        )

    async def get_pending(self, session: AsyncSession, event_id: str) -> NotificationCommand | None:
        """Decode one still-pending event for delivery."""
        row: NotificationOutboxRow | None = await session.get(NotificationOutboxRow, event_id)
        return (
            decode_notification_command(row.payload_json)
            if row is not None and row.delivered_at is None
            else None
        )

    async def acknowledge(self, session: AsyncSession, event_id: str, at: datetime) -> None:
        """Record the consumer's committed receipt."""
        await session.execute(
            update(NotificationOutboxRow)
            .where(
                NotificationOutboxRow.id == event_id,
                NotificationOutboxRow.delivered_at.is_(None),
            )
            .values(delivered_at=at)
        )
