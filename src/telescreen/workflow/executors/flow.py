"""Executors for workflow routing, timers, child workflows, and termination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from telescreen.core.json import json_bool, json_object, json_str
from telescreen.core.vocabulary import ErrorCode, StepType
from telescreen.ports.workflow import StepSignal, StepSignalKind
from telescreen.workflow.dsl.expressions import EvalResult, evaluate
from telescreen.workflow.dsl.model import (
    ConditionStep,
    EndStep,
    JoinStep,
    Outcomes3,
    ParallelStep,
    SendBackStep,
    StepSpec,
    SubWorkflowStep,
    WaitStep,
)
from telescreen.workflow.dsl.templates import render
from telescreen.workflow.step_api import StepContext, StepOutcome, WaitKind


class RunStarter(Protocol):
    """Start a child workflow from a subworkflow executor."""

    async def start_child(self, ctx: StepContext, step: SubWorkflowStep) -> str:
        """Create a child run and return its durable id."""
        ...


@dataclass
class ConditionExecutor:
    """Route a step through the first true expression case."""

    step_type: StepType = StepType.CONDITION

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Evaluate condition cases in order."""
        step: ConditionStep = _expect(ctx.step, ConditionStep)
        for case in step.cases:
            result: EvalResult = evaluate(case.when, ctx.context)
            if result.value.as_bool():
                return StepOutcome.completed(
                    json_object(
                        (
                            ("matched", json_bool(True)),
                            ("note", json_str(result.note or "")),
                        )
                    ),
                    case.next,
                )
        return StepOutcome.completed(json_object((("matched", json_bool(False)),)), step.default)

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Condition steps cannot be resumed."""
        del ctx, signal
        return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)


@dataclass
class WaitExecutor:
    """Wait for a timer and then continue to the configured next step."""

    step_type: StepType = StepType.WAIT

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Return a timer wait at the step's duration deadline."""
        step: WaitStep = _expect(ctx.step, WaitStep)
        return StepOutcome.waiting(WaitKind.TIMER, ctx.now + step.duration)

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Continue only after a timer signal."""
        step: WaitStep = _expect(ctx.step, WaitStep)
        if signal.kind is not StepSignalKind.TIMER_FIRED:
            return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)
        return StepOutcome.completed(json_object(()), step.next)


@dataclass
class SendBackExecutor:
    """Return a bounded send-back request to the engine."""

    step_type: StepType = StepType.SEND_BACK

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Render a masked send-back reason and return its target."""
        step: SendBackStep = _expect(ctx.step, SendBackStep)
        return StepOutcome.completed(
            json_object((("reason", json_str(render(step.reason, ctx.context))),)),
            step.to,
        )

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Send-back steps do not wait."""
        del ctx, signal
        return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)


@dataclass
class ParallelExecutor:
    """Mark a fork point; the engine creates branch visits."""

    step_type: StepType = StepType.PARALLEL

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Return the branch marker to the engine."""
        step: ParallelStep = _expect(ctx.step, ParallelStep)
        return StepOutcome.completed(json_object((("join", json_str(step.join)),)))

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Parallel markers do not wait directly."""
        del ctx, signal
        return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)


@dataclass
class JoinExecutor:
    """Mark a join point; the engine owns arrival tracking."""

    step_type: StepType = StepType.JOIN

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Return a join wait to the engine."""
        del ctx
        return StepOutcome.waiting(WaitKind.JOIN)

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Continue when the engine signals join completion."""
        step: JoinStep = _expect(ctx.step, JoinStep)
        if signal.kind is not StepSignalKind.BRANCH_ARRIVED:
            return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)
        return StepOutcome.completed(json_object(()), step.next)


@dataclass
class SubWorkflowExecutor:
    """Start a child workflow and wait for its terminal result."""

    starter: RunStarter
    step_type: StepType = StepType.SUB_WORKFLOW

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Start the child and return a child wait."""
        step: SubWorkflowStep = _expect(ctx.step, SubWorkflowStep)
        await self.starter.start_child(ctx, step)
        return StepOutcome.waiting(WaitKind.CHILD)

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Route a child completion into the declared outcome target."""
        step: SubWorkflowStep = _expect(ctx.step, SubWorkflowStep)
        if signal.kind is not StepSignalKind.CHILD_FINISHED or signal.outcome is None:
            return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)
        outcomes: Outcomes3 = step.outcomes
        route: str | None = None
        if signal.outcome == "succeeded":
            route = outcomes.succeeded
        elif signal.outcome == "failed":
            route = outcomes.failed
        elif signal.outcome == "error":
            route = outcomes.error
        if route is None:
            return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)
        return StepOutcome.completed(json_object((("outcome", json_str(signal.outcome)),)), route)


@dataclass
class EndExecutor:
    """Return a terminal run result to the engine."""

    step_type: StepType = StepType.END

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Return the end result encoded as a safe output field."""
        step: EndStep = _expect(ctx.step, EndStep)
        return StepOutcome.completed(
            json_object((("result", json_str(step.result.value)),)),
            step.result.value,
        )

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """End steps cannot be resumed."""
        del ctx, signal
        return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)


def _expect[T: StepSpec](step: StepSpec, expected: type[T]) -> T:
    """Return the typed step or fail with a static error."""
    if not isinstance(step, expected):
        from telescreen.core.errors import TelescreenError

        raise TelescreenError(ErrorCode.WORKFLOW_INVALID, "workflow step type mismatch")
    return step
