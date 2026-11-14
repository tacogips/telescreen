"""Worker delivery and recovery polling for the inventory outbox."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind, EngineTaskStatus, ErrorCode
from telescreen.db.models.workflow import EngineTaskRow
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.ports.inventory_commands import (
    InventoryCommand,
    InventoryCommandReceiver,
    InventoryReceipt,
)
from telescreen.ports.workflow import ClaimedTask, TaskHandler, TaskQueue, TaskResult, TaskSpec


@dataclass(frozen=True)
class InventoryDeliveryHandler(TaskHandler):
    """Deliver the task's command using independent producer and inventory commits."""

    sessions: async_sessionmaker[AsyncSession]
    outbox: InventoryOutbox
    receiver: InventoryCommandReceiver
    clock: Clock
    kind: EngineTaskKind = EngineTaskKind.INVENTORY_DELIVERY

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """A lost receipt or acknowledgment retries the same immutable event identity."""
        async with self.sessions() as session:
            command: InventoryCommand | None = await self.outbox.get_pending(session, task.ref_id)
        if command is None:
            return TaskResult.done()
        receipt: InventoryReceipt = await self.receiver.receive(command)
        if receipt is InventoryReceipt.GAP:
            return TaskResult.retry(self.clock.now() + timedelta(seconds=5), ErrorCode.CONFLICT)
        async with self.sessions.begin() as session:
            await self.outbox.acknowledge(session, command.event_id, self.clock.now())
        return TaskResult.done()


@dataclass
class InventoryRecoveryProvider:
    """Recover abandoned or exhausted tasks without discarding outbox commands."""

    outbox: InventoryOutbox
    queue: TaskQueue
    after_stream_id: str | None = None
    batch_size: int = 100

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Walk stream heads fairly; enqueue at most one recovery per event and minute."""
        commands: tuple[InventoryCommand, ...] = await self.outbox.pending(
            session, self.batch_size, self.after_stream_id
        )
        if not commands and self.after_stream_id is not None:
            self.after_stream_id = None
            commands = await self.outbox.pending(session, self.batch_size)
        if commands:
            self.after_stream_id = commands[-1].stream_id
        minute: datetime = ensure_utc(now).replace(second=0, microsecond=0)
        created: int = 0
        for command in commands:
            active: bool = bool(
                await session.scalar(
                    select(
                        exists().where(
                            EngineTaskRow.kind == EngineTaskKind.INVENTORY_DELIVERY.value,
                            EngineTaskRow.ref_id == command.event_id,
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
                        EngineTaskKind.INVENTORY_DELIVERY,
                        command.event_id,
                        json_null(),
                        now,
                        f"inventory-recovery:{command.event_id}:{minute.isoformat()}",
                    ),
                )
            )
        return created
