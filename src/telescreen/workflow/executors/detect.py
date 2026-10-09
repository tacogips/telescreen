"""Workflow executor for rescanning the resource attached to an issue."""

from dataclasses import dataclass

from telescreen.core.json import JsonValue, json_object, json_str
from telescreen.core.vocabulary import ErrorCode, StepType
from telescreen.ports.collectors import RescanKind, RescanResult, ResourceRescanner
from telescreen.ports.workflow import StepSignal
from telescreen.workflow.dsl.model import DetectStep
from telescreen.workflow.step_api import StepContext, StepOutcome


@dataclass(frozen=True)
class DetectExecutor:
    """Rescan an issue resource and route the safe result."""

    rescanner: ResourceRescanner
    step_type: StepType = StepType.DETECT

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Rescan and return a declared outcome route."""
        step: DetectStep = _detect_step(ctx)
        if ctx.issue_id is None:
            return _result("error", step.outcomes.error, ErrorCode.NOT_RESCANNABLE)
        try:
            result: RescanResult = await self.rescanner.rescan(ctx.issue_id, step.detectors)
        except Exception:
            return _result("error", step.outcomes.error, ErrorCode.FETCH_FAILED)
        if result.kind is RescanKind.FOUND:
            return _result("found", step.outcomes.found, result.error_code)
        if result.kind is RescanKind.CLEAN:
            return _result("clean", step.outcomes.clean, result.error_code)
        return _result("error", step.outcomes.error, result.error_code or ErrorCode.FETCH_FAILED)

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Detect steps do not wait for signals."""
        del ctx, signal
        return StepOutcome.failed(ErrorCode.INVALID_TRANSITION, retryable=False)


def _detect_step(ctx: StepContext) -> DetectStep:
    """Return the typed detect step from a validated workflow context."""
    if not isinstance(ctx.step, DetectStep):
        raise TypeError("executor step type mismatch")
    return ctx.step


def _result(result: str, route: str | None, error_code: ErrorCode | None) -> StepOutcome:
    """Build a safe outcome object containing only a result and stable code."""
    members: list[tuple[str, JsonValue]] = [("result", json_str(result))]
    if error_code is not None:
        members.append(("error_code", json_str(error_code.value)))
    return StepOutcome.completed(json_object(members), route)
