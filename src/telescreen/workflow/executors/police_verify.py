"""Workflow executor for asynchronous Police verification tasks."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import ScalarResult

from telescreen.core.json import JsonKind, JsonValue, json_int, json_object, json_str
from telescreen.core.vocabulary import ErrorCode, PoliceTaskStatus, StepType
from telescreen.db.models.police import PoliceTaskRow
from telescreen.masking.redact import Redactor
from telescreen.ports.police import PoliceGateway, PoliceTaskRequest
from telescreen.ports.workflow import StepSignal, StepSignalKind
from telescreen.workflow.dsl.context import IssueContext
from telescreen.workflow.dsl.model import NamedTemplate, PoliceVerifyStep
from telescreen.workflow.dsl.templates import render
from telescreen.workflow.step_api import StepContext, StepOutcome, WaitKind

_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        PoliceTaskStatus.COMPLETED.value,
        PoliceTaskStatus.FAILED.value,
        PoliceTaskStatus.EXPIRED.value,
        PoliceTaskStatus.CANCELLED.value,
    }
)


@dataclass(frozen=True)
class PoliceVerifyExecutor:
    """Dispatch a task and wait for a Police result or timeout."""

    gateway: PoliceGateway
    redactor: Redactor
    step_type: StepType = StepType.POLICE_VERIFY

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Dispatch a safely rendered task with a fixed deadline."""
        step: PoliceVerifyStep = _police_step(ctx)
        deadline: datetime = ctx.now + step.timeout
        task_input: JsonValue = _task_input(ctx, step, self.redactor)
        request: PoliceTaskRequest = PoliceTaskRequest(
            task_type=step.task_type,
            labels=step.labels,
            input=task_input,
            deadline=deadline,
            step_run_id=ctx.step_run_id,
        )
        await self.gateway.dispatch(ctx.session, request)
        return StepOutcome.waiting(WaitKind.POLICE, timer_at=deadline)

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Route a Police result or cancel an outstanding task on timeout."""
        step: PoliceVerifyStep = _police_step(ctx)
        if signal.kind is StepSignalKind.TIMER_FIRED:
            await _cancel_open_task(ctx, self.gateway)
            return StepOutcome.completed(
                json_object((("timed_out", JsonValue(JsonKind.BOOL, bool_value=True)),)),
                step.outcomes.error,
            )
        if signal.kind is not StepSignalKind.POLICE_RESULT:
            return StepOutcome.failed(ErrorCode.INVALID_TRANSITION, retryable=False)
        if signal.outcome == "error":
            return StepOutcome.completed(_police_output(signal), step.outcomes.error)
        if signal.outcome == "passed":
            return StepOutcome.completed(_police_output(signal), step.outcomes.passed)
        if signal.outcome == "failed":
            return StepOutcome.completed(_police_output(signal), step.outcomes.failed)
        return StepOutcome.completed(
            json_object((("outcome", json_str("error")),)), step.outcomes.error
        )


def _police_step(ctx: StepContext) -> PoliceVerifyStep:
    """Return the typed Police step from a validated workflow context."""
    if not isinstance(ctx.step, PoliceVerifyStep):
        raise TypeError("executor step type mismatch")
    return ctx.step


def _task_input(ctx: StepContext, step: PoliceVerifyStep, redactor: Redactor) -> JsonValue:
    """Build the task-type-specific payload using redacted context templates."""
    if step.task_type == "verify_remediation":
        issue: IssueContext | None = ctx.context.issue
        if issue is None:
            return json_object((("issue", json_object(())), ("instructions", json_str(""))))
        instructions: str = _render_named(step.input, "instructions", ctx, redactor)
        return json_object(
            (
                (
                    "issue",
                    json_object(
                        (
                            ("id", json_str(ctx.issue_id or "")),
                            ("short_id", json_str(issue.short_id)),
                            ("source", json_str(issue.source)),
                            ("display_locator", json_str(issue.display_locator)),
                            ("rule_class", json_str(issue.rule_class)),
                            ("severity", json_str(issue.severity)),
                            ("first_seen", json_str(issue.first_seen)),
                            ("last_seen", json_str(issue.last_seen)),
                        )
                    ),
                ),
                ("instructions", json_str(instructions)),
            )
        )
    rendered: list[tuple[str, JsonValue]] = []
    for item in step.input:
        rendered.append((item.name, json_str(_render(item, ctx, redactor))))
    if step.task_type.startswith("custom:"):
        return json_object((("payload", json_object(rendered)),))
    return json_object(rendered)


def _render_named(
    templates: tuple[NamedTemplate, ...], name: str, ctx: StepContext, redactor: Redactor
) -> str:
    """Render one named template, returning empty text when it is absent."""
    item: NamedTemplate | None = next((part for part in templates if part.name == name), None)
    return _render(item, ctx, redactor) if item is not None else ""


def _render(item: NamedTemplate, ctx: StepContext, redactor: Redactor) -> str:
    """Render and redact one outbound template string."""
    return redactor.redact(render(item.template, ctx.context)).text


async def _cancel_open_task(ctx: StepContext, gateway: PoliceGateway) -> None:
    """Cancel nonterminal Police task rows associated with this step run."""
    result: ScalarResult[PoliceTaskRow] = await ctx.session.scalars(
        select(PoliceTaskRow).where(PoliceTaskRow.step_run_id == ctx.step_run_id)
    )
    rows: list[PoliceTaskRow] = list(result.all())
    for row in rows:
        if row.status not in _TERMINAL_STATUSES:
            await gateway.cancel(ctx.session, row.id)


def _police_output(signal: StepSignal) -> JsonValue:
    """Select only the supported safe fields from a Police result signal."""
    data: JsonValue = signal.data
    summary_value: JsonValue | None = data.get("summary")
    count_value: JsonValue | None = data.get("findings_count")
    summary: str | None = summary_value.as_str() if summary_value is not None else None
    findings_count: int | None = count_value.as_int() if count_value is not None else None
    return json_object(
        (
            ("outcome", json_str(signal.outcome or "error")),
            ("summary", json_str(summary or "")),
            ("findings_count", json_int(findings_count or 0)),
        )
    )
