"""Human approval and evidence upload workflow executors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import (
    EngineTaskKind,
    HumanTaskKind,
    NotificationChannel,
    StepType,
)
from telescreen.db.models import EscalationPolicyRow, HumanTaskRow
from telescreen.masking.redact import Redactor
from telescreen.ports.directory import RecipientResolver, ResolvedRecipients
from telescreen.ports.notifications import NotificationRequest, NotificationSender
from telescreen.ports.workflow import StepSignal, StepSignalKind, TaskSpec
from telescreen.workflow.dsl.model import EvidenceUploadStep, HumanTaskStep, NamedRoute
from telescreen.workflow.dsl.templates import render
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from telescreen.workflow.step_api import StepContext, StepOutcome, WaitKind


@dataclass
class HumanTaskExecutor:
    """Create approval or evidence tasks and resume from a human outcome."""

    notifications: NotificationSender
    queue: SqlTaskQueue
    resolver: RecipientResolver
    clock: Clock
    redactor: Redactor
    step_type: StepType = StepType.HUMAN_TASK

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Persist a human task and schedule its notification and SLA check."""
        if isinstance(ctx.step, HumanTaskStep):
            step: HumanTaskStep = ctx.step
            kind: HumanTaskKind = HumanTaskKind.APPROVAL
            title: str = render(step.title, ctx.context)
            assignee: str = step.assignee
            sla: timedelta | None = step.sla
            policy_name: str | None = step.escalation_policy
            notify: bool = step.notify_on_assign
        elif isinstance(ctx.step, EvidenceUploadStep):
            step_evidence: EvidenceUploadStep = ctx.step
            kind = HumanTaskKind.EVIDENCE
            title = render(step_evidence.title, ctx.context)
            assignee = step_evidence.assignee
            sla = step_evidence.sla
            policy_name = step_evidence.escalation_policy
            notify = True
        else:
            from telescreen.core.errors import TelescreenError
            from telescreen.core.vocabulary import ErrorCode

            raise TelescreenError(ErrorCode.WORKFLOW_INVALID, "workflow step type mismatch")
        safe_title: str = self.redactor.redact(title).text[:512]
        safe_assignee: str = self.redactor.redact(assignee).text[:256]
        policy_id: str | None = None
        if policy_name is not None:
            policy: EscalationPolicyRow | None = await ctx.session.scalar(
                select(EscalationPolicyRow).where(EscalationPolicyRow.name == policy_name)
            )
            policy_id = policy.id if policy is not None else None
        human_task_id: str = new_id()
        row: HumanTaskRow = HumanTaskRow(
            id=human_task_id,
            run_id=ctx.run_id,
            step_run_id=ctx.step_run_id,
            kind=kind.value,
            title=safe_title,
            assignee_ref=safe_assignee,
            escalation_policy_id=policy_id,
            tier_index=0,
            due_at=ctx.now + sla if sla is not None else None,
            status="open",
        )
        ctx.session.add(row)
        await ctx.session.flush()
        if notify:
            await self._notify(ctx, human_task_id, assignee, safe_title)
        if sla is not None:
            await self.queue.enqueue(
                ctx.session,
                TaskSpec(
                    EngineTaskKind.SLA_CHECK,
                    human_task_id,
                    json_object((("human_task_id", json_str(human_task_id)),)),
                    ctx.now + sla,
                    f"sla:{human_task_id}:0",
                ),
            )
        return StepOutcome.waiting(WaitKind.HUMAN)

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Route a completed approval or evidence task to its declared outcome."""
        if signal.kind is not StepSignalKind.HUMAN_COMPLETED or signal.outcome is None:
            from telescreen.core.vocabulary import ErrorCode

            return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)
        if isinstance(ctx.step, HumanTaskStep):
            routes: tuple[NamedRoute, ...] = ctx.step.outcomes
            route: str | None = next(
                (item.to for item in routes if item.name == signal.outcome),
                None,
            )
        elif isinstance(ctx.step, EvidenceUploadStep):
            route = ctx.step.next if signal.outcome in ("accepted", "completed") else None
        else:
            route = None
        if route is None:
            from telescreen.core.vocabulary import ErrorCode

            return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)
        return StepOutcome.completed(
            json_object((("outcome", json_str(signal.outcome)),)),
            route,
        )

    async def _notify(self, ctx: StepContext, task_id: str, assignee: str, title: str) -> None:
        """Queue email and Slack notifications using resolved contacts."""
        recipients: ResolvedRecipients = await self.resolver.resolve(ctx.session, assignee)
        if recipients.emails:
            await self.notifications.send(
                ctx.session,
                NotificationRequest(
                    NotificationChannel.EMAIL,
                    recipients.emails,
                    "Workflow task assigned",
                    title,
                    "system:workflow",
                    ctx.run_id,
                    ctx.issue_id,
                ),
            )
        slack_targets: tuple[str, ...] = recipients.slack_channels + recipients.slack_user_ids
        if slack_targets:
            await self.notifications.send(
                ctx.session,
                NotificationRequest(
                    NotificationChannel.SLACK,
                    slack_targets,
                    "Workflow task assigned",
                    title,
                    "system:workflow",
                    ctx.run_id,
                    ctx.issue_id,
                ),
            )


@dataclass
class EvidenceUploadExecutor(HumanTaskExecutor):
    """Register evidence upload tasks under their dedicated DSL step type."""

    step_type: StepType = StepType.EVIDENCE_UPLOAD
