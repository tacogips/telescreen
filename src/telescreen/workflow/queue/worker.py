"""Polling worker for durable engine tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.vocabulary import EngineTaskKind, EngineTaskStatus, ErrorCode
from telescreen.ports.workflow import (
    ClaimedTask,
    TaskHandler,
    TaskResult,
)
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from telescreen.workflow.scheduler.service import Scheduler

logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class TaskHandlerRegistry:
    """Register handlers by durable task kind."""

    handlers: list[tuple[EngineTaskKind, TaskHandler]] = field(default_factory=list)

    def register(self, handler: TaskHandler) -> None:
        """Register a task handler, replacing an earlier binding."""
        self.handlers = [(kind, item) for kind, item in self.handlers if kind is not handler.kind]
        self.handlers.append((handler.kind, handler))

    def get(self, kind: EngineTaskKind) -> TaskHandler | None:
        """Find a handler for a task kind."""
        return next((handler for registered, handler in self.handlers if registered is kind), None)


class DeadTaskHook(Protocol):
    """Hook notified when a task reaches its terminal retry limit."""

    async def on_dead(self, task: ClaimedTask) -> None:
        """Handle a terminal task failure."""
        ...


@dataclass
class EngineWorker:
    """Claim, dispatch, and settle engine tasks using short transactions."""

    sessions: async_sessionmaker[AsyncSession]
    queue: SqlTaskQueue
    registry: TaskHandlerRegistry
    scheduler: Scheduler
    clock: Clock
    worker_id: str
    concurrency: int = 8
    poll_seconds: float = 1.0
    dead_hook: DeadTaskHook | None = None
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep
    lease: timedelta = timedelta(seconds=60)

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until stopped, reclaiming leases and ticking schedules each pass."""
        while not stop.is_set():
            async with self.sessions.begin() as session:
                await self.queue.reclaim_expired(session, self.clock.now())
            await self.scheduler.tick()
            claimed: list[ClaimedTask] = await self._claim()
            if claimed:
                await asyncio.gather(*(self._process(task) for task in claimed))
            else:
                await self.sleeper(self.poll_seconds)

    async def run_until_idle(self, max_rounds: int = 1000) -> int:
        """Process due work without sleeping and return the task count."""
        processed: int = 0
        for _ in range(max_rounds):
            async with self.sessions.begin() as session:
                await self.queue.reclaim_expired(session, self.clock.now())
            await self.scheduler.tick()
            claimed: list[ClaimedTask] = await self._claim()
            if not claimed:
                return processed
            await asyncio.gather(*(self._process(task) for task in claimed))
            processed += len(claimed)
        return processed

    async def _claim(self) -> list[ClaimedTask]:
        """Claim one batch in a transaction."""
        async with self.sessions.begin() as session:
            return await self.queue.claim(
                session,
                self.worker_id,
                tuple(EngineTaskKind),
                self.concurrency,
                self.lease,
            )

    async def _process(self, task: ClaimedTask) -> None:
        """Dispatch one task and persist its disposition."""
        handler: TaskHandler | None = self.registry.get(task.kind)
        if handler is None:
            result: TaskResult = TaskResult.dead(ErrorCode.WORKFLOW_INVALID)
        else:
            try:
                result = await handler.handle(task)
            except Exception:
                delay: timedelta = timedelta(seconds=10 * (2 ** (task.attempt - 1)))
                logger.error("engine task failed id=%s kind=%s", task.id, task.kind.value)
                result = TaskResult.retry(
                    self.clock.now() + delay,
                    ErrorCode.INTERNAL_ERROR,
                )
        final_status: EngineTaskStatus | None = None
        async with self.sessions.begin() as session:
            final_status = await self.queue.apply_result(session, task.id, result)
        if final_status is EngineTaskStatus.DEAD and self.dead_hook is not None:
            await self.dead_hook.on_dead(task)
