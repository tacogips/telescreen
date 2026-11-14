"""Durable worker delivery from inventory retention publication to the notification owner."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind, EngineTaskStatus
from telescreen.db.models.workflow import EngineTaskRow
from telescreen.inventory.retention_outbox import RetentionOutbox
from telescreen.ports.retention import RetentionCommand, RetentionReceiver
from telescreen.ports.workflow import ClaimedTask, TaskHandler, TaskQueue, TaskResult, TaskSpec


@dataclass(frozen=True)
class RetentionDeliveryHandler(TaskHandler):
    """Receive and acknowledge with separate notification and inventory transactions."""

    sessions: async_sessionmaker[AsyncSession]
    outbox: RetentionOutbox
    receiver: RetentionReceiver
    clock: Clock
    kind: EngineTaskKind = EngineTaskKind.RETENTION_DELIVERY

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """A lost acknowledgment retries the original retention command identity."""
        async with self.sessions() as session:
            command: RetentionCommand | None = await self.outbox.get_pending(session, task.ref_id)
        if command is None:
            return TaskResult.done()
        await self.receiver.receive(command)
        async with self.sessions.begin() as session:
            await self.outbox.acknowledge(session, command.event_id, self.clock.now())
        return TaskResult.done()


@dataclass
class RetentionRecoveryProvider:
    """Poll committed inventory commands without relying on an inventory task queue."""

    outbox: RetentionOutbox
    queue: TaskQueue
    source_sessions: async_sessionmaker[AsyncSession] | None = None
    after_id: str | None = None
    batch_size: int = 100

    async def _pending(self, session: AsyncSession) -> tuple[str, ...]:
        """Use the local unit of work only when both tables share the configured store."""
        if self.source_sessions is None:
            return await self.outbox.pending_ids(session, self.batch_size, self.after_id)
        async with self.source_sessions() as source:
            return await self.outbox.pending_ids(source, self.batch_size, self.after_id)

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Rotate pending IDs; preserve active retries and recover exhausted tasks."""
        identifiers: tuple[str, ...] = await self._pending(session)
        if not identifiers and self.after_id is not None:
            self.after_id = None
            identifiers = await self._pending(session)
        if identifiers:
            self.after_id = identifiers[-1]
        created: int = 0
        minute: datetime = ensure_utc(now).replace(second=0, microsecond=0)
        for event_id in identifiers:
            active: bool = bool(
                await session.scalar(
                    select(
                        exists().where(
                            EngineTaskRow.kind == EngineTaskKind.RETENTION_DELIVERY.value,
                            EngineTaskRow.ref_id == event_id,
                            EngineTaskRow.status.in_(
                                (EngineTaskStatus.READY.value, EngineTaskStatus.LEASED.value)
                            ),
                        )
                    )
                )
            )
            if active:
                continue
            created += int(
                await self.queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.RETENTION_DELIVERY,
                        event_id,
                        json_null(),
                        now,
                        f"inventory-retention:{event_id}:{minute.isoformat()}",
                    ),
                )
            )
        return created
