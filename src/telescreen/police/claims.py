"""Pull-worker claims, leases, and heartbeats."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.vocabulary import PoliceTaskStatus, PoliceWorkerStatus
from telescreen.db.models import PoliceTaskRow, PoliceWorkerRow
from telescreen.police.gateway import matching_pull_tasks


@dataclass(frozen=True)
class ClaimRequest:
    """Validated long-poll claim parameters."""

    max_tasks: int
    wait_seconds: int
    task_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaimService:
    """Lease compatible queued tasks without holding sessions during sleeps."""

    clock: Clock
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def claim(
        self,
        sessions: async_sessionmaker[AsyncSession],
        worker: PoliceWorkerRow,
        request: ClaimRequest,
    ) -> tuple[PoliceTaskRow, ...]:
        """Poll until tasks arrive or the bounded wait expires."""
        if worker.mode != "pull" or worker.status != PoliceWorkerStatus.ACTIVE.value:
            return ()
        types: tuple[str, ...] = request.task_types or tuple(
            filter(None, worker.task_types.split(","))
        )
        limit: int = min(request.max_tasks, worker.concurrency)
        deadline: datetime = ensure_utc(self.clock.now()) + timedelta(seconds=request.wait_seconds)
        while True:
            claimed_ids: tuple[str, ...] = ()
            async with sessions.begin() as session:
                now: datetime = ensure_utc(self.clock.now())
                active_worker: PoliceWorkerRow | None = await session.get(
                    PoliceWorkerRow, worker.id
                )
                if active_worker is None or active_worker.status != PoliceWorkerStatus.ACTIVE.value:
                    return ()
                tasks: tuple[PoliceTaskRow, ...] = await matching_pull_tasks(
                    session, active_worker, types, limit
                )
                for task in tasks:
                    task.status = PoliceTaskStatus.LEASED.value
                    task.worker_id = active_worker.id
                    task.attempt += 1
                    task.lease_expires_at = now + timedelta(seconds=60)
                if tasks:
                    active_worker.last_heartbeat_at = now
                    claimed_ids = tuple(task.id for task in tasks)
            if claimed_ids:
                async with sessions() as session:
                    refreshed: list[PoliceTaskRow] = []
                    for task_id in claimed_ids:
                        task = await session.get(PoliceTaskRow, task_id)
                        if task is not None:
                            refreshed.append(task)
                    return tuple(refreshed)
            now: datetime = ensure_utc(self.clock.now())
            if now >= deadline:
                return ()
            await self.sleeper(min(1.0, (deadline - now).total_seconds()))

    async def heartbeat(
        self,
        session: AsyncSession,
        worker: PoliceWorkerRow,
        task_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Extend owned active leases and return cancelled ids among the request."""
        now: datetime = ensure_utc(self.clock.now())
        cancelled: list[str] = []
        for task_id in task_ids:
            task: PoliceTaskRow | None = await session.get(PoliceTaskRow, task_id)
            if task is None or task.worker_id != worker.id:
                continue
            if task.status == PoliceTaskStatus.CANCELLED.value:
                cancelled.append(task_id)
            elif task.status == PoliceTaskStatus.LEASED.value:
                task.lease_expires_at = now + timedelta(seconds=60)
        worker.last_heartbeat_at = now
        return tuple(cancelled)
