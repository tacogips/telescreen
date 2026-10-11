"""Human approval and evidence upload workflow executors."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sqlalchemy import select

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import (
    EngineTaskKind,
    ErrorCode,
    HumanTaskKind,
    NotificationChannel,
    NotificationEvent,
    StepType,
)
from telescreen.db.models import EscalationPolicyRow, HumanTaskRow
from telescreen.masking.redact import Redactor
from telescreen.ports.directory import RecipientResolver
from telescreen.ports.issues import IssueReader, IssueSnapshot
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.workflow import StepSignal, StepSignalKind, TaskSpec
from telescreen.workflow.dsl.model import (
    EvidenceUploadStep,
    HumanTaskStep,
    NamedRoute,
    NotificationTargetSpec,
)
from telescreen.workflow.dsl.templates import render
from telescreen.workflow.notify_targets import FanOutText, build_workflow_context, fan_out
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from telescreen.workflow.recipients import ReachableRef, reachable_refs, refs_for_channel
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
    issues: IssueReader | None = None
    public_base_url: str | None = None

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Persist a human task and schedule its notification and SLA check."""
        if isinstance(ctx.step, HumanTaskStep):
            step: HumanTaskStep = ctx.step
            kind: HumanTaskKind = step.kind
            assignee: str = step.assignee
            issue_snapshot: IssueSnapshot | None = None
            if kind is HumanTaskKind.OWNER_REVIEW or assignee == "issue:assignee":
                if ctx.issue_id is None or self.issues is None:
                    return StepOutcome.failed(ErrorCode.RECIPIENT_UNKNOWN, False)
                issue_snapshot = await self.issues.get(ctx.session, ctx.issue_id)
                if issue_snapshot is None:
                    return StepOutcome.failed(ErrorCode.RECIPIENT_UNKNOWN, False)
            if assignee == "issue:assignee":
                if issue_snapshot is None or issue_snapshot.assignee_ref is None:
                    return StepOutcome.failed(ErrorCode.RECIPIENT_UNKNOWN, False)
                assignee = issue_snapshot.assignee_ref
            title: str = (
                f"Telescreen: review TELE-{issue_snapshot.number}"
                if kind is HumanTaskKind.OWNER_REVIEW and issue_snapshot is not None
                else render(step.title, ctx.context)
            )
            sla: timedelta | None = step.sla
            policy_name: str | None = step.escalation_policy
            notify: bool = step.notify_on_assign
        elif isinstance(ctx.step, EvidenceUploadStep):
            step_evidence: EvidenceUploadStep = ctx.step
            issue_snapshot = None
            kind = HumanTaskKind.EVIDENCE
            title = render(step_evidence.title, ctx.context)
            assignee = step_evidence.assignee
            sla = step_evidence.sla
            policy_name = step_evidence.escalation_policy
            notify = True
        else:
            raise TelescreenError(ErrorCode.WORKFLOW_INVALID, "workflow step type mismatch")
        safe_title: str = self.redactor.redact(title).text[:512]
        safe_assignee: str = self.redactor.redact(assignee).text[:256]
        policy_id: str | None = None
        if policy_name is not None:
            policy: EscalationPolicyRow | None = await ctx.session.scalar(
                select(EscalationPolicyRow).where(EscalationPolicyRow.name == policy_name)
            )
            policy_id = policy.id if policy is not None else None
        if issue_snapshot is None and self.issues is not None and ctx.issue_id is not None:
            issue_snapshot = await self.issues.get(ctx.session, ctx.issue_id)
        owner_review: bool = kind is HumanTaskKind.OWNER_REVIEW
        due_at: datetime | None = ctx.now + sla if sla is not None else None
        context: NotificationContext = build_workflow_context(
            issue_snapshot,
            ctx.context.run.workflow,
            ctx.step.name or ctx.step.id,
            safe_title,
            due_at,
            None,
            safe_assignee,
            self.public_base_url,
        )
        if owner_review:
            context = replace(context, issue_title=None, issue_location=None, issue_excerpt=None)
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
            due_at=due_at,
            status="open",
        )
        ctx.session.add(row)
        await ctx.session.flush()
        if notify:
            await self._notify(ctx, assignee, context, owner_review)
        await self._notify_targets(ctx, context)
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

    async def _notify(
        self,
        ctx: StepContext,
        assignee: str,
        context: NotificationContext,
        owner_review: bool,
    ) -> None:
        """Queue assignee notifications by recipient reference on every reachable channel.

        The sender resolves references at delivery time, so the assignee reference itself is
        sent. An unknown assignee is skipped so the human task still opens. Text comes from
        the event template and the masked context (owner reviews omit title and locator).
        """
        reachable: tuple[ReachableRef, ...] = await reachable_refs(
            self.resolver, ctx.session, (assignee,)
        )
        if owner_review and assignee.startswith("member:"):
            channel: NotificationChannel = (
                NotificationChannel.SLACK
                if refs_for_channel(reachable, NotificationChannel.SLACK)
                else NotificationChannel.EMAIL
            )
            refs: tuple[str, ...] = refs_for_channel(reachable, channel)
            if refs and context.issue_short_id is not None:
                await self.notifications.send(
                    ctx.session,
                    NotificationRequest(
                        channel,
                        refs,
                        "",
                        "",
                        "system:workflow",
                        ctx.run_id,
                        ctx.issue_id,
                        event=NotificationEvent.OWNER_REVIEW_ASSIGNED,
                        context=context,
                    ),
                )
            return
        for channel in (NotificationChannel.EMAIL, NotificationChannel.SLACK):
            refs: tuple[str, ...] = refs_for_channel(reachable, channel)
            if refs:
                await self.notifications.send(
                    ctx.session,
                    NotificationRequest(
                        channel,
                        refs,
                        "",
                        "",
                        "system:workflow",
                        ctx.run_id,
                        ctx.issue_id,
                        event=NotificationEvent.HUMAN_TASK_ASSIGNED,
                        context=context,
                    ),
                )

    async def _notify_targets(self, ctx: StepContext, context: NotificationContext) -> None:
        """Fan out the step's notification set or inline targets when the task opens.

        Unknown or unreachable recipients become skipped rows and never fail the task.
        """
        step: HumanTaskStep | EvidenceUploadStep = _task_step(ctx)
        targets: tuple[NotificationTargetSpec, ...] = step.set_targets or step.notify_targets
        if not targets:
            return
        await fan_out(
            self.notifications,
            ctx.session,
            targets,
            step.notify_set if step.set_targets else None,
            FanOutText(NotificationEvent.HUMAN_TASK_ASSIGNED, "system.human_task_assigned"),
            context,
            "system:workflow",
            ctx.run_id,
            ctx.issue_id,
        )


def _task_step(ctx: StepContext) -> HumanTaskStep | EvidenceUploadStep:
    """Return the typed human or evidence step of a validated workflow context."""
    if isinstance(ctx.step, (HumanTaskStep, EvidenceUploadStep)):
        return ctx.step
    raise TelescreenError(ErrorCode.WORKFLOW_INVALID, "workflow step type mismatch")


@dataclass
class EvidenceUploadExecutor(HumanTaskExecutor):
    """Register evidence upload tasks under their dedicated DSL step type."""

    step_type: StepType = StepType.EVIDENCE_UPLOAD
