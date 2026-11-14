"""Workflow executor for DLP-gated notifications."""

from dataclasses import dataclass

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_array, json_bool, json_object, json_str
from telescreen.core.vocabulary import DlpVerdict, ErrorCode, StepType
from telescreen.masking.redact import Redactor
from telescreen.ports.notifications import (
    NotificationReceipt,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.workflow import StepSignal
from telescreen.workflow.dsl.model import NotifyStep
from telescreen.workflow.dsl.templates import render
from telescreen.workflow.step_api import StepContext, StepOutcome


@dataclass(frozen=True)
class NotifyExecutor:
    """Queue a masked notification through the injected sender port."""

    sender: NotificationSender
    redactor: Redactor
    step_type: StepType = StepType.NOTIFY

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Render, redact and queue a notification without waiting for delivery."""
        step: NotifyStep = _notify_step(ctx)
        request: NotificationRequest = NotificationRequest(
            channel=step.channel,
            recipients=step.to,
            subject=self.redactor.redact(render(step.subject, ctx.context)).text,
            body=self.redactor.redact(render(step.body, ctx.context)).text,
            actor_ref="system:workflow",
            run_id=ctx.run_id,
            issue_id=ctx.issue_id,
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

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Notification steps do not wait for signals."""
        del ctx, signal
        return StepOutcome.failed(ErrorCode.INVALID_TRANSITION, retryable=False)


def _notify_step(ctx: StepContext) -> NotifyStep:
    """Return the typed notify step from a validated workflow context."""
    if not isinstance(ctx.step, NotifyStep):
        raise TypeError("executor step type mismatch")
    return ctx.step
