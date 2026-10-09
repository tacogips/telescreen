"""Frozen executor API shared by the workflow engine and step implementations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonValue, json_null
from telescreen.core.vocabulary import ErrorCode, StepType
from telescreen.ports.workflow import StepSignal
from telescreen.workflow.dsl.context import RunContext
from telescreen.workflow.dsl.model import StepSpec


class StepOutcomeKind(StrEnum):
    """Executor outcome disposition."""

    COMPLETED = "completed"
    WAITING = "waiting"
    FAILED = "failed"


class WaitKind(StrEnum):
    """Reason that a step is waiting for external progress."""

    HUMAN = "human"
    TIMER = "timer"
    POLICE = "police"
    CHILD = "child"
    JOIN = "join"
    MAP = "map"


@dataclass(frozen=True)
class StepOutcome:
    """Safe executor result returned to the durable engine."""

    kind: StepOutcomeKind
    output: JsonValue
    route: str | None
    wait_kind: WaitKind | None
    timer_at: datetime | None
    error_code: ErrorCode | None
    retryable: bool

    @staticmethod
    def completed(output: JsonValue, route: str | None = None) -> StepOutcome:
        """Return a completed step result."""
        return StepOutcome(StepOutcomeKind.COMPLETED, output, route, None, None, None, False)

    @staticmethod
    def waiting(wait_kind: WaitKind, timer_at: datetime | None = None) -> StepOutcome:
        """Return a waiting step result."""
        return StepOutcome(
            StepOutcomeKind.WAITING, json_null(), None, wait_kind, timer_at, None, False
        )

    @staticmethod
    def failed(error_code: ErrorCode, retryable: bool) -> StepOutcome:
        """Return a failed step result with a safe error code."""
        return StepOutcome(
            StepOutcomeKind.FAILED, json_null(), None, None, None, error_code, retryable
        )


@dataclass(frozen=True)
class StepContext:
    """Execution inputs available to a step executor."""

    session: AsyncSession
    run_id: str
    step_run_id: str
    step: StepSpec
    visit: int
    attempt: int
    context: RunContext
    issue_id: str | None
    now: datetime


class StepExecutor(Protocol):
    """Execute and resume one registered workflow step type."""

    step_type: StepType

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Execute a new step visit."""
        ...

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Resume a waiting step from a persisted signal."""
        ...
