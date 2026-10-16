"""Inventory-owned outbox delivery and overdue publication without a bureau queue."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from croniter import croniter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind
from telescreen.inventory.retention import DataOverdueHandler
from telescreen.ports.workflow import ClaimedTask

logger: logging.Logger = logging.getLogger(__name__)


class Outbox[T](Protocol):
    """Only detached pending commands cross an owner boundary."""

    async def pending_ids(
        self, session: AsyncSession, limit: int, after_id: str | None = None
    ) -> tuple[str, ...]: ...

    async def get_pending(self, session: AsyncSession, event_id: str) -> T | None: ...

    async def acknowledge(self, session: AsyncSession, event_id: str, at: datetime) -> None: ...


@dataclass
class OutboxPump[T, R]:
    """Rotate bounded batches and acknowledge only validated committed remote receipts."""

    sessions: async_sessionmaker[AsyncSession]
    outbox: Outbox[T]
    receive: Callable[[T], Awaitable[R]]
    clock: Clock
    after_id: str | None = None
    batch_size: int = 20

    async def tick(self) -> None:
        """Failed commands remain durable; later IDs still get a delivery attempt."""
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
                    command: T | None = await self.outbox.get_pending(session, event_id)
                if command is None:
                    continue
                await self.receive(command)
                async with self.sessions.begin() as session:
                    await self.outbox.acknowledge(session, event_id, self.clock.now())
            except Exception as error:
                logger.warning("inventory outbox delivery failed type=%s", type(error).__name__)


@dataclass
class InventoryWorker:
    """Run publication, local audit and independent delivery loops in inventory."""

    audit_tick: Callable[[], Awaitable[None]]
    retention_tick: Callable[[], Awaitable[None]]
    notification_tick: Callable[[], Awaitable[None]]
    overdue: DataOverdueHandler
    clock: Clock
    cron: str
    poll_seconds: float
    last_minute: datetime | None = None

    async def publish_due(self) -> None:
        """Rechecking a minute after restart is safe because publication is idempotent."""
        now: datetime = ensure_utc(self.clock.now())
        if not self.cron:
            return
        fire: datetime = (
            now
            if croniter.match(self.cron, now)
            else ensure_utc(croniter(self.cron, now).get_prev(datetime))
        )
        minute: datetime = fire.replace(second=0, microsecond=0)
        if self.last_minute == minute or now - fire > timedelta(hours=1):
            return
        await self.overdue.handle(
            ClaimedTask(
                "overdue", EngineTaskKind.DATA_OVERDUE_CHECK, "overdue", json_null(), 1, 1, ""
            )
        )
        self.last_minute = minute

    async def _loop(self, tick: Callable[[], Awaitable[None]]) -> None:
        """Outages do not terminate publication or delivery; cancellation stays immediate."""
        while True:
            try:
                await tick()
            except Exception as error:
                logger.warning("inventory worker tick failed type=%s", type(error).__name__)
            await asyncio.sleep(self.poll_seconds)

    async def run(self) -> None:
        """Keep each independent function progressing during another owner's outage."""
        async with asyncio.TaskGroup() as group:
            group.create_task(self._loop(self.publish_due))
            group.create_task(self._loop(self.audit_tick))
            group.create_task(self._loop(self.retention_tick))
            group.create_task(self._loop(self.notification_tick))
