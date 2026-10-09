"""Workflow engine ports and immutable queue DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonValue, json_null
from telescreen.core.vocabulary import EngineTaskKind, ErrorCode


class StepSignalKind(StrEnum):
    """External events that can resume a waiting workflow step."""

    HUMAN_COMPLETED = "human_completed"
    EVIDENCE_ACCEPTED = "evidence_accepted"
    POLICE_RESULT = "police_result"
    TIMER_FIRED = "timer_fired"
    CHILD_FINISHED = "child_finished"
    BRANCH_ARRIVED = "branch_arrived"
    MAP_FINISHED = "map_finished"


@dataclass(frozen=True)
class StepSignal:
    """Safe signal payload for resuming a workflow step."""

    kind: StepSignalKind
    outcome: str | None = None
    data: JsonValue = field(default_factory=json_null)


class RunSignaler(Protocol):
    """Signal a waiting workflow step in the caller transaction."""

    async def signal(self, session: AsyncSession, step_run_id: str, signal: StepSignal) -> None:
        """Record a signal and schedule step advancement."""
        ...


@dataclass(frozen=True)
class TaskSpec:
    """Durable task enqueue request."""

    kind: EngineTaskKind
    ref_id: str
    payload: JsonValue
    available_at: datetime
    idempotency_key: str
    max_attempts: int = 5


@dataclass(frozen=True)
class ClaimedTask:
    """Leased task passed to a handler."""

    id: str
    kind: EngineTaskKind
    ref_id: str
    payload: JsonValue
    attempt: int
    max_attempts: int
    idempotency_key: str


class TaskResultKind(StrEnum):
    """Disposition returned by a durable task handler."""

    DONE = "done"
    RETRY = "retry"
    DEAD = "dead"


@dataclass(frozen=True)
class TaskResult:
    """Safe task disposition and optional retry information."""

    kind: TaskResultKind
    available_at: datetime | None = None
    error_code: ErrorCode | None = None

    @staticmethod
    def done() -> TaskResult:
        """Return a successful task disposition."""
        return TaskResult(TaskResultKind.DONE)

    @staticmethod
    def retry(available_at: datetime, error_code: ErrorCode) -> TaskResult:
        """Return a retry disposition with its next availability time."""
        return TaskResult(TaskResultKind.RETRY, available_at, error_code)

    @staticmethod
    def dead(error_code: ErrorCode) -> TaskResult:
        """Return a terminal failure disposition."""
        return TaskResult(TaskResultKind.DEAD, error_code=error_code)


class TaskQueue(Protocol):
    """Enqueue and cancel idempotent engine tasks."""

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Enqueue a task; return false for a duplicate idempotency key."""
        ...

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Cancel ready tasks with the supplied reference id."""
        ...


class TaskHandler(Protocol):
    """Idempotent handler for one durable engine task kind."""

    kind: EngineTaskKind

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Handle a task in the handler's own transaction."""
        ...


class ScheduleProvider(Protocol):
    """Create due scheduled work inside the caller transaction."""

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Create due work and return the count created."""
        ...
