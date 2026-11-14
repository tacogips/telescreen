"""Durable inventory notification commands and receipt-aware remote delivery."""

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx
from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.secrets import SecretValue
from telescreen.db.models.notification_handoff import InventoryNotificationOutboxRow
from telescreen.inventory_service.transport import NOTIFICATION_PREFIX, ServiceHttp
from telescreen.ports.notification_commands import (
    NotificationCommand,
    NotificationCommandReceipt,
    decode_notification_command,
    encode_notification_command,
)

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationOutbox:
    """Store immutable commands in the inventory producer transaction."""

    async def append(self, session: AsyncSession, command: NotificationCommand) -> None:
        """Persist one validated command under its deterministic event identity."""
        session.add(
            InventoryNotificationOutboxRow(
                id=command.event_id,
                payload_json=encode_notification_command(command),
                recorded_at=command.occurred_at,
            )
        )
        await session.flush()

    async def pending_ids(
        self, session: AsyncSession, limit: int, after_id: str | None = None
    ) -> tuple[str, ...]:
        """Read pending command IDs in stable order."""
        return tuple(
            (
                await session.scalars(
                    select(InventoryNotificationOutboxRow.id)
                    .where(
                        InventoryNotificationOutboxRow.delivered_at.is_(None),
                        InventoryNotificationOutboxRow.id > (after_id or ""),
                    )
                    .order_by(InventoryNotificationOutboxRow.id)
                    .limit(limit)
                )
            ).all()
        )

    async def get_pending(self, session: AsyncSession, event_id: str) -> NotificationCommand | None:
        """Decode a pending command before the HTTP request begins."""
        row: InventoryNotificationOutboxRow | None = await session.get(
            InventoryNotificationOutboxRow, event_id
        )
        return (
            decode_notification_command(row.payload_json)
            if row is not None and row.delivered_at is None
            else None
        )

    async def acknowledge(self, session: AsyncSession, event_id: str, at: datetime) -> None:
        """Acknowledge a committed Notification receipt."""
        await session.execute(
            update(InventoryNotificationOutboxRow)
            .where(
                InventoryNotificationOutboxRow.id == event_id,
                InventoryNotificationOutboxRow.delivered_at.is_(None),
            )
            .values(delivered_at=ensure_utc(at))
        )


@dataclass
class NotificationOutboxPump:
    """Deliver commands and retain deferred or failed work for retry."""

    sessions: async_sessionmaker[AsyncSession]
    outbox: NotificationOutbox
    transport: ServiceHttp
    clock: Clock
    after_id: str | None = None
    batch_size: int = 20

    async def tick(self) -> None:
        """Post detached commands and acknowledge only applied or duplicate receipts."""
        async with self.sessions() as session:
            identifiers: tuple[str, ...] = await self.outbox.pending_ids(
                session, self.batch_size, self.after_id
            )
            if not identifiers and self.after_id is not None:
                self.after_id = None
                identifiers = await self.outbox.pending_ids(session, self.batch_size)
        for event_id in identifiers:
            self.after_id = event_id
            try:
                async with self.sessions() as session:
                    command: NotificationCommand | None = await self.outbox.get_pending(
                        session, event_id
                    )
                if command is None:
                    continue
                receipt: NotificationCommandReceipt = await self.transport.post(
                    "/commands",
                    encode_notification_command(command),
                    TypeAdapter(NotificationCommandReceipt),
                )
                if receipt is NotificationCommandReceipt.DEFERRED:
                    continue
                async with self.sessions.begin() as session:
                    await self.outbox.acknowledge(session, event_id, self.clock.now())
            except Exception as error:
                logger.warning(
                    "inventory notification delivery failed type=%s", type(error).__name__
                )


def notification_transport(
    http: httpx.AsyncClient, base_url: str, secret: SecretValue
) -> ServiceHttp:
    """Create fixed-origin Notification transport with the notification route prefix."""
    return ServiceHttp(http, base_url, secret, NOTIFICATION_PREFIX)
