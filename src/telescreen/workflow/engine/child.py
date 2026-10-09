"""Durable child-workflow completion delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.vocabulary import EngineTaskKind, ErrorCode, RunStatus
from telescreen.db.models import WorkflowRunRow
from telescreen.ports.workflow import (
    ClaimedTask,
    RunSignaler,
    StepSignal,
    StepSignalKind,
    TaskResult,
)
from telescreen.workflow.engine.repository import WorkflowRepository


@dataclass
class ChildRunFinishedHandler:
    """Signal a parent step after its child run reaches a terminal state."""

    sessions: async_sessionmaker[AsyncSession]
    repository: WorkflowRepository
    signaler: RunSignaler
    clock: Clock
    kind: EngineTaskKind = EngineTaskKind.CHILD_RUN_FINISHED

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Signal the waiting parent idempotently."""
        async with self.sessions.begin() as session:
            child: WorkflowRunRow | None = await self.repository.run(session, task.ref_id)
            if child is None or child.parent_step_run_id is None:
                return TaskResult.done()
            if child.status not in (
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
                RunStatus.CANCELLED.value,
            ):
                return TaskResult.retry(
                    self.clock.now() + timedelta(seconds=10),
                    ErrorCode.INTERNAL_ERROR,
                )
            outcome: str = "succeeded" if child.status == RunStatus.SUCCEEDED.value else "failed"
            await self.signaler.signal(
                session,
                child.parent_step_run_id,
                StepSignal(StepSignalKind.CHILD_FINISHED, outcome),
            )
        return TaskResult.done()
