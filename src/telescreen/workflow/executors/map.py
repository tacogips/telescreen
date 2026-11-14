"""Workflow executor for the durable map DSL step."""

from dataclasses import dataclass

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, MapOrigin
from telescreen.ports.collectors import CollectorConfig, CollectorConfigReader
from telescreen.ports.workflow import StepSignal, StepSignalKind
from telescreen.workflow.dsl.model import MapStep
from telescreen.workflow.mapreduce.service import MapJobService
from telescreen.workflow.mapreduce.spec import spec_from_step
from telescreen.workflow.step_api import StepContext, StepOutcome, WaitKind


@dataclass(frozen=True)
class MapExecutor:
    """Create a map job and resume its workflow step on map_finished."""

    service: MapJobService
    configs: CollectorConfigReader

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Create the durable job for a validated map step."""
        if not isinstance(ctx.step, MapStep):
            return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)
        config: CollectorConfig | None = await self.configs.get_by_name(
            ctx.session, ctx.step.collector
        )
        if config is None:
            return StepOutcome.failed(ErrorCode.NOT_FOUND, False)
        try:
            await self.service.create_job(
                ctx.session,
                MapOrigin.WORKFLOW,
                config.id,
                ctx.step_run_id,
                spec_from_step(ctx.step),
            )
        except TelescreenError as error:
            return StepOutcome.failed(error.code, False)
        return StepOutcome.waiting(WaitKind.MAP)

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Route the map result by its terminal outcome."""
        if signal.kind is not StepSignalKind.MAP_FINISHED or not isinstance(ctx.step, MapStep):
            return StepOutcome.failed(ErrorCode.INVALID_TRANSITION, False)
        outcome: str = signal.outcome or "failed"
        route: str | None = (
            ctx.step.outcomes.succeeded
            if outcome == "succeeded"
            else ctx.step.outcomes.partial
            if outcome == "partial"
            else ctx.step.outcomes.failed
            if outcome == "failed"
            else None
        )
        return StepOutcome.completed(signal.data, route)
