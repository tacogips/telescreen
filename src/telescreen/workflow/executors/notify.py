"""Workflow executor for DLP-gated notifications."""

from dataclasses import dataclass

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_array, json_bool, json_object, json_str
from telescreen.core.vocabulary import DlpVerdict, ErrorCode, NotificationEvent, StepType
from telescreen.masking.redact import Redactor
from telescreen.ports.issues import IssueReader, IssueSnapshot
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationReceipt,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.workflow import StepSignal
from telescreen.workflow.dsl.model import NotificationTargetSpec, NotifyStep, Template
from telescreen.workflow.dsl.templates import render
from telescreen.workflow.notify_targets import (
    FanOutText,
    TargetOutcome,
    build_workflow_context,
    fan_out,
    outcomes_json,
    with_run_issue,
)
from telescreen.workflow.step_api import StepContext, StepOutcome

_DEFAULT_TEMPLATE: str = "system.workflow_notify"


@dataclass(frozen=True)
class NotifyExecutor:
    """Queue a masked notification through the injected sender port.

    The v1 ``to`` form sends rendered inline text without an event. A v2 ``to`` form with a
    template, a notification set or inline targets sends ``workflow_notify`` requests with a
    masked context; set and target forms fan out one request per target and channel.
    """

    sender: NotificationSender
    redactor: Redactor
    step_type: StepType = StepType.NOTIFY
    issues: IssueReader | None = None
    public_base_url: str | None = None

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Render, redact and queue notifications without waiting for delivery."""
        step: NotifyStep = _notify_step(ctx)
        targets: tuple[NotificationTargetSpec, ...] = step.set_targets or step.targets
        if step.notification_set is None and not step.targets:
            return await self._send_to(ctx, step)
        context: NotificationContext = await self._context(ctx, step)
        outcomes: tuple[TargetOutcome, ...] = await fan_out(
            self.sender,
            ctx.session,
            targets,
            step.notification_set,
            FanOutText(
                NotificationEvent.WORKFLOW_NOTIFY,
                _DEFAULT_TEMPLATE,
                step.template,
                self._inline(step.subject, ctx),
                self._inline(step.body, ctx),
            ),
            context,
            "system:workflow",
            ctx.run_id,
            ctx.issue_id,
        )
        return StepOutcome.completed(outcomes_json(outcomes), step.next)

    async def _send_to(self, ctx: StepContext, step: NotifyStep) -> StepOutcome:
        """Send the ``to`` form: v1 inline text, or (v2) a named workflow template."""
        if step.template is None:
            request: NotificationRequest = NotificationRequest(
                channel=step.channel,
                recipients=step.to,
                subject=self.redactor.redact(render(step.subject, ctx.context)).text,
                body=self.redactor.redact(render(step.body, ctx.context)).text,
                actor_ref="system:workflow",
                run_id=ctx.run_id,
                issue_id=ctx.issue_id,
            )
        else:
            request = NotificationRequest(
                channel=step.channel,
                recipients=step.to,
                subject="",
                body="",
                actor_ref="system:workflow",
                run_id=ctx.run_id,
                issue_id=ctx.issue_id,
                event=NotificationEvent.WORKFLOW_NOTIFY,
                template_name=step.template,
                context=await self._context(ctx, step),
            )
        try:
            receipt: NotificationReceipt = await self.sender.send(ctx.session, request)
        except TelescreenError as error:
            if error.code is ErrorCode.RECIPIENT_UNKNOWN:
                return StepOutcome.failed(ErrorCode.RECIPIENT_UNKNOWN, retryable=False)
            return StepOutcome.failed(error.code, retryable=True)
        if not receipt.queued or receipt.verdict is DlpVerdict.BLOCKED:
            output: JsonValue = json_object(
                (("queued", json_bool(False)), ("delivered", json_bool(False)))
            )
        else:
            output = json_object(
                (
                    ("queued", json_bool(True)),
                    (
                        "notification_ids",
                        json_array(tuple(json_str(item) for item in receipt.notification_ids)),
                    ),
                )
            )
        return StepOutcome.completed(output, step.next)

    async def _context(self, ctx: StepContext, step: NotifyStep) -> NotificationContext:
        """Build the masked template context from the issue snapshot or the run context."""
        issue: IssueSnapshot | None = (
            await self.issues.get(ctx.session, ctx.issue_id)
            if self.issues is not None and ctx.issue_id is not None
            else None
        )
        context: NotificationContext = build_workflow_context(
            issue,
            ctx.context.run.workflow,
            step.name or step.id,
            None,
            None,
            None,
            None,
            self.public_base_url,
        )
        return with_run_issue(context, ctx.context.issue)

    def _inline(self, template: Template, ctx: StepContext) -> str | None:
        """Render and redact optional inline text; empty templates are absent."""
        if not template:
            return None
        return self.redactor.redact(render(template, ctx.context)).text

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Notification steps do not wait for signals."""
        del ctx, signal
        return StepOutcome.failed(ErrorCode.INVALID_TRANSITION, retryable=False)


def _notify_step(ctx: StepContext) -> NotifyStep:
    """Return the typed notify step from a validated workflow context."""
    if not isinstance(ctx.step, NotifyStep):
        raise TypeError("executor step type mismatch")
    return ctx.step
