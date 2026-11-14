"""Concurrent pull-mode Police worker loop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field

from telescreen_police_sdk.client import BureauClient
from telescreen_police_sdk.models import (
    ClaimRequest,
    ResultEnvelope,
    ResultError,
    ResultStatus,
    TaskEnvelope,
)

type TaskHandler = Callable[[TaskEnvelope], Awaitable[ResultEnvelope]]


@dataclass
class PullWorker:
    """Claim tasks up to the concurrency limit and heartbeat active work."""

    client: BureauClient
    worker_id: str
    token: str = field(repr=False)
    handlers: tuple[tuple[str, TaskHandler], ...]
    concurrency: int
    heartbeat_interval: float
    wait_seconds: int
    _active: list[tuple[str, asyncio.Task[None]]] = field(
        default_factory=list, init=False, repr=False
    )

    async def run(self, stop: asyncio.Event) -> None:
        """Run until stopped, then finish active handlers gracefully."""
        heartbeat: asyncio.Task[None] = asyncio.create_task(self._heartbeat_loop(stop))
        try:
            while not stop.is_set():
                self._discard_finished()
                free_slots: int = self.concurrency - len(self._active)
                if free_slots <= 0:
                    await self._wait_for_activity(stop)
                    continue
                claim: ClaimRequest = ClaimRequest(free_slots, self.wait_seconds)
                tasks: tuple[TaskEnvelope, ...] = await self.client.claim(
                    self.worker_id, self.token, claim
                )
                for envelope in tasks:
                    if len(self._active) >= self.concurrency:
                        break
                    task: asyncio.Task[None] = asyncio.create_task(self._execute(envelope))
                    self._active.append((envelope.task_id, task))
            if self._active:
                await asyncio.gather(*(task for _, task in self._active), return_exceptions=True)
        finally:
            stop.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _execute(self, envelope: TaskEnvelope) -> None:
        """Run one handler and submit a safe result."""
        handler: TaskHandler | None = next(
            (candidate for name, candidate in self.handlers if name == envelope.task_type), None
        )
        if handler is None:
            result: ResultEnvelope = self._failed(envelope.task_id, "unsupported_task")
        else:
            try:
                result = await handler(envelope)
            except asyncio.CancelledError:
                raise
            except Exception:
                result = self._failed(envelope.task_id, "handler_error")
        await self.client.submit_result(self.token, result)

    async def _heartbeat_loop(self, stop: asyncio.Event) -> None:
        """Heartbeat active tasks and cancel those withdrawn by the Bureau."""
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_interval)
                return
            except TimeoutError:
                self._discard_finished()
                task_ids: tuple[str, ...] = tuple(task_id for task_id, _ in self._active)
                if not task_ids:
                    continue
                cancelled: tuple[str, ...] = await self.client.heartbeat(
                    self.worker_id, self.token, task_ids
                )
                cancelled_set: frozenset[str] = frozenset(cancelled)
                for task_id, task in self._active:
                    if task_id in cancelled_set:
                        task.cancel()

    async def _wait_for_activity(self, stop: asyncio.Event) -> None:
        """Wait for a running task to finish or for shutdown."""
        active_tasks: tuple[asyncio.Task[None], ...] = tuple(
            task for _, task in self._active if not task.done()
        )
        if not active_tasks:
            return
        stop_waiter: asyncio.Task[bool] = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait(active_tasks + (stop_waiter,), return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await stop_waiter

    def _discard_finished(self) -> None:
        """Remove completed tasks from the active list."""
        self._active[:] = [(task_id, task) for task_id, task in self._active if not task.done()]

    @staticmethod
    def _failed(task_id: str, code: str) -> ResultEnvelope:
        """Construct a failure without including exception details."""
        return ResultEnvelope(
            task_id, ResultStatus.FAILED, error=ResultError(code, "Task handler failed")
        )
