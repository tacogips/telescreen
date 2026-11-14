"""Leased delivery pump for Notification-owned queued rows."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind, NotificationStatus
from telescreen.db.models import NotificationDeliveryStateRow, NotificationRow
from telescreen.notifications.delivery import NotificationDeliveryHandler
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryPump:
    """Claim due delivery schedules, run handlers, and persist retry disposition."""

    sessions: async_sessionmaker[AsyncSession]
    delivery: NotificationDeliveryHandler
    clock: Clock
    poll_seconds: float = 1.0
    lease_seconds: int = 60

    async def tick(self) -> int:
        """Deliver one due batch and return the number of handlers called."""
        now: datetime = ensure_utc(self.clock.now())
        async with self.sessions.begin() as session:
            if session.get_bind().dialect.name == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            queued: tuple[NotificationRow, ...] = tuple(
                (
                    await session.scalars(
                        select(NotificationRow).where(
                            NotificationRow.status == NotificationStatus.QUEUED.value
                        )
                    )
                ).all()
            )
            for notification in queued:
                state: NotificationDeliveryStateRow | None = await session.get(
                    NotificationDeliveryStateRow, notification.id
                )
                if state is None:
                    session.add(
                        NotificationDeliveryStateRow(
                            notification_id=notification.id,
                            attempt=0,
                            max_attempts=5,
                            available_at=now,
                            leased_until=None,
                        )
                    )
            await session.flush()
            due: tuple[NotificationDeliveryStateRow, ...] = tuple(
                (
                    await session.scalars(
                        select(NotificationDeliveryStateRow)
                        .where(
                            NotificationDeliveryStateRow.available_at <= now,
                            (NotificationDeliveryStateRow.leased_until.is_(None))
                            | (NotificationDeliveryStateRow.leased_until <= now),
                        )
                        .order_by(NotificationDeliveryStateRow.available_at)
                        .limit(32)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claimed: tuple[ClaimedTask, ...] = tuple(
                ClaimedTask(
                    state.notification_id,
                    EngineTaskKind.NOTIFICATION_DELIVERY,
                    state.notification_id,
                    json_null(),
                    max(1, state.attempt + 1),
                    state.max_attempts,
                    f"notify:{state.notification_id}",
                )
                for state in due
            )
            for state in due:
                state.leased_until = now + timedelta(seconds=self.lease_seconds)
            await session.flush()
        for task in claimed:
            result: TaskResult = await self.delivery.handle(task)
            async with self.sessions.begin() as session:
                state = await session.get(NotificationDeliveryStateRow, task.ref_id)
                if state is None:
                    continue
                if result.kind in (TaskResultKind.DONE, TaskResultKind.DEAD):
                    await session.delete(state)
                else:
                    state.attempt = task.attempt
                    state.available_at = result.available_at or self.clock.now()
                    state.leased_until = None
        return len(claimed)

    async def run(self) -> None:
        """Poll continuously until the owning application cancels this task."""
        while True:
            try:
                await self.tick()
            except Exception as error:
                logger.warning("notification delivery tick failed type=%s", type(error).__name__)
            await asyncio.sleep(self.poll_seconds)
