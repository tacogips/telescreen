"""Durable notification command delivery and recovery."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind, EngineTaskStatus, ErrorCode
from telescreen.db.models.workflow import EngineTaskRow
from telescreen.integrations.notification_outbox import NotificationOutbox
from telescreen.ports.notification_commands import NotificationCommand, NotificationCommandReceipt
from telescreen.ports.workflow import (
    ClaimedTask,
    ScheduleProvider,
    TaskHandler,
    TaskQueue,
    TaskResult,
    TaskSpec,
)


class NotificationCommandReceiver(Protocol):
    """Transport that delivers a typed command to the notification owner."""

    async def receive(self, command: NotificationCommand) -> NotificationCommandReceipt:
        """Return only after a durable consumer receipt is available."""
        ...


@dataclass(frozen=True)
class NotificationCommandDeliveryHandler(TaskHandler):
    """Deliver pending commands and acknowledge only committed consumer outcomes."""

    sessions: async_sessionmaker[AsyncSession]
    outbox: NotificationOutbox
    receiver: NotificationCommandReceiver
    clock: Clock
    kind: EngineTaskKind = EngineTaskKind.NOTIFICATION_COMMAND_DELIVERY

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Retry deferred or failed deliveries while preserving event identity."""
        async with self.sessions() as session:
            command: NotificationCommand | None = await self.outbox.get_pending(
                session, task.ref_id
            )
        if command is None:
            return TaskResult.done()
        try:
            receipt: NotificationCommandReceipt = await self.receiver.receive(command)
        except TelescreenError:
            delay: int = min(300, 2 ** min(task.attempt, 8))
            return TaskResult.retry(
                self.clock.now() + timedelta(seconds=delay), ErrorCode.SERVICE_UNAVAILABLE
            )
        if receipt is NotificationCommandReceipt.DEFERRED:
            return TaskResult.retry(self.clock.now() + timedelta(seconds=30), ErrorCode.CONFLICT)
        async with self.sessions.begin() as session:
            await self.outbox.acknowledge(session, command.event_id, self.clock.now())
        return TaskResult.done()


@dataclass
class NotificationRecoveryProvider(ScheduleProvider):
    """Re-enqueue pending outbox rows after task loss or exhaustion."""

    outbox: NotificationOutbox
    queue: TaskQueue
    after_id: str | None = None
    batch_size: int = 100

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Rotate through pending event ids and restore missing active tasks."""
        identifiers: tuple[str, ...] = await self.outbox.pending_ids(
            session, self.batch_size, self.after_id
        )
        if not identifiers and self.after_id is not None:
            self.after_id = None
            identifiers = await self.outbox.pending_ids(session, self.batch_size)
        if identifiers:
            self.after_id = identifiers[-1]
        minute: datetime = ensure_utc(now).replace(second=0, microsecond=0)
        created: int = 0
        for event_id in identifiers:
            active: bool = bool(
                await session.scalar(
                    select(
                        exists().where(
                            EngineTaskRow.kind
                            == EngineTaskKind.NOTIFICATION_COMMAND_DELIVERY.value,
                            EngineTaskRow.ref_id == event_id,
                            EngineTaskRow.status.in_(
                                (EngineTaskStatus.READY.value, EngineTaskStatus.LEASED.value)
                            ),
                        )
                    )
                )
            )
            if not active:
                created += int(
                    await self.queue.enqueue(
                        session,
                        TaskSpec(
                            EngineTaskKind.NOTIFICATION_COMMAND_DELIVERY,
                            event_id,
                            json_null(),
                            now,
                            f"notification-recovery:{event_id}:{minute.isoformat()}",
                        ),
                    )
                )
        return created


@dataclass(frozen=True)
class NotificationDeliveryNoop(TaskHandler):
    """Complete legacy per-notification tasks when split mode uses command delivery."""

    kind: EngineTaskKind = EngineTaskKind.NOTIFICATION_DELIVERY

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Retire legacy work without contacting the notification service."""
        del task
        return TaskResult.done()
