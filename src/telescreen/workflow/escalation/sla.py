"""Durable human-task SLA escalation handler."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_object, json_str, parse_json
from telescreen.core.vocabulary import (
    ActorKind,
    EngineTaskKind,
    HumanTaskKind,
    IssueHistoryKind,
    NotificationEvent,
)
from telescreen.db.models import (
    EscalationPolicyRow,
    HumanTaskRow,
    StepRunRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import RecipientResolver
from telescreen.ports.issues import IssueHistoryEntry, IssueReader, IssueRecorder, IssueSnapshot
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskSpec
from telescreen.workflow.dsl.escalation import (
    EscalationPolicySpec,
    EscalationTier,
    escalation_from_json,
)
from telescreen.workflow.dsl.loader import compiled_from_json
from telescreen.workflow.dsl.model import CompiledWorkflow, NotificationSetSpec
from telescreen.workflow.dsl.notification_sets import find_set
from telescreen.workflow.engine.repository import WorkflowRepository
from telescreen.workflow.notify_targets import FanOutText, build_workflow_context, fan_out
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from telescreen.workflow.recipients import ReachableRef, reachable_refs, refs_for_channel


@dataclass
class SlaCheckHandler:
    """Advance an overdue human task through its configured escalation tiers."""

    sessions: async_sessionmaker[AsyncSession]
    repository: WorkflowRepository
    queue: SqlTaskQueue
    notifications: NotificationSender
    resolver: RecipientResolver
    issues: IssueRecorder
    audit: AuditLog
    clock: Clock
    kind: EngineTaskKind = EngineTaskKind.SLA_CHECK
    reader: IssueReader | None = None
    public_base_url: str | None = None

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Escalate only tasks that remain open when the SLA task runs."""
        async with self.sessions.begin() as session:
            human_task: HumanTaskRow | None = await session.get(HumanTaskRow, task.ref_id)
            if human_task is None or human_task.status != "open":
                return TaskResult.done()
            run: WorkflowRunRow | None = await self.repository.run(session, human_task.run_id)
            if run is None:
                return TaskResult.done()
            policy_row: EscalationPolicyRow | None = (
                await session.get(EscalationPolicyRow, human_task.escalation_policy_id)
                if human_task.escalation_policy_id is not None
                else None
            )
            policy: EscalationPolicySpec | None = (
                escalation_from_json(parse_json(policy_row.tiers_json))
                if policy_row is not None
                else None
            )
            now: datetime = self.clock.now()
            tier: EscalationTier | None = (
                policy.tiers[human_task.tier_index]
                if policy is not None and human_task.tier_index < len(policy.tiers)
                else None
            )
            if tier is not None and tier.min_risk is not None and run.issue_id is not None:
                issue: IssueSnapshot | None = (
                    await self.reader.get(session, run.issue_id)
                    if self.reader is not None
                    else None
                )
                if issue is not None and issue.risk_score < tier.min_risk:
                    await self.issues.add_history(
                        session,
                        IssueHistoryEntry(
                            run.issue_id,
                            IssueHistoryKind.SLA_BREACHED,
                            "system:workflow",
                            now,
                            "below risk threshold",
                        ),
                    )
                    await self._audit(session, human_task, run, "sla.breached")
                    return TaskResult.done()
            if run.issue_id is not None:
                await self.issues.add_history(
                    session,
                    IssueHistoryEntry(
                        run.issue_id,
                        IssueHistoryKind.SLA_BREACHED,
                        "system:workflow",
                        now,
                        "Human task SLA breached",
                    ),
                )
            if tier is None:
                await self._audit(session, human_task, run, "sla.breached")
                return TaskResult.done()
            human_task.assignee_ref = tier.assign_to
            human_task.tier_index += 1
            due_at: datetime = now + tier.after
            human_task.due_at = due_at
            await self._send_tier(session, human_task, run, tier, due_at)
            await self._audit(session, human_task, run, "sla.escalated")
            await self.queue.enqueue(
                session,
                TaskSpec(
                    EngineTaskKind.SLA_CHECK,
                    human_task.id,
                    json_object((("human_task_id", json_str(human_task.id)),)),
                    due_at,
                    f"sla:{human_task.id}:{human_task.tier_index}",
                ),
            )
        return TaskResult.done()

    async def _send_tier(
        self,
        session: AsyncSession,
        human_task: HumanTaskRow,
        run: WorkflowRunRow,
        tier: EscalationTier,
        due_at: datetime,
    ) -> None:
        """Enqueue DLP-gated tier notifications, then fan out the tier's set and targets.

        Tier ``notify`` references are passed through unresolved (the sender resolves them at
        delivery); unknown recipients are skipped so the escalation itself still completes.
        The tier set is resolved against the run's stored workflow; a missing set is audited
        as ``notification.set_missing`` and skipped.
        """
        definition: WorkflowDefinitionRow | None = await self.repository.definition(
            session, run.definition_id
        )
        context: NotificationContext = await self._context(
            session, human_task, run, definition, tier, due_at
        )
        reachable: tuple[ReachableRef, ...] = await reachable_refs(
            self.resolver, session, tier.notify
        )
        for channel in tier.channels:
            channel_refs: tuple[str, ...] = refs_for_channel(reachable, channel)
            if channel_refs:
                await self.notifications.send(
                    session,
                    NotificationRequest(
                        channel,
                        channel_refs,
                        "",
                        "",
                        "system:workflow",
                        run.id,
                        run.issue_id,
                        event=NotificationEvent.SLA_ESCALATED,
                        context=context,
                    ),
                )
        text: FanOutText = FanOutText(NotificationEvent.SLA_ESCALATED, "system.sla_escalated")
        if tier.notification_set is not None:
            notification_set: NotificationSetSpec | None = _tier_set(
                definition, tier.notification_set
            )
            if notification_set is None:
                await self.audit.append(
                    session,
                    AuditEntry(
                        ActorKind.SYSTEM,
                        "system:workflow",
                        "notification.set_missing",
                        "human_task",
                        human_task.id,
                        "Escalation notification set missing",
                        json_object((("set_name", json_str(tier.notification_set)),)),
                    ),
                )
            else:
                await fan_out(
                    self.notifications,
                    session,
                    notification_set.targets,
                    notification_set.name,
                    text,
                    context,
                    "system:workflow",
                    run.id,
                    run.issue_id,
                )
        if tier.targets:
            await fan_out(
                self.notifications,
                session,
                tier.targets,
                None,
                text,
                context,
                "system:workflow",
                run.id,
                run.issue_id,
            )

    async def _context(
        self,
        session: AsyncSession,
        human_task: HumanTaskRow,
        run: WorkflowRunRow,
        definition: WorkflowDefinitionRow | None,
        tier: EscalationTier,
        due_at: datetime,
    ) -> NotificationContext:
        """Build the masked escalation context (owner reviews omit title and locator)."""
        issue: IssueSnapshot | None = (
            await self.reader.get(session, run.issue_id)
            if self.reader is not None and run.issue_id is not None
            else None
        )
        step_run: StepRunRow | None = await self.repository.step_run(
            session, human_task.step_run_id
        )
        context: NotificationContext = build_workflow_context(
            issue,
            definition.name if definition is not None else "",
            step_run.step_id if step_run is not None else "",
            human_task.title,
            due_at,
            human_task.tier_index,
            tier.assign_to,
            self.public_base_url,
        )
        if human_task.kind == HumanTaskKind.OWNER_REVIEW.value:
            context = replace(context, issue_title=None, issue_location=None, issue_excerpt=None)
        return context

    async def _audit(
        self,
        session: AsyncSession,
        human_task: HumanTaskRow,
        run: WorkflowRunRow,
        action: str,
    ) -> None:
        """Append a safe escalation audit entry."""
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:workflow",
                action,
                "human_task",
                human_task.id,
                "Human task SLA breached",
                json_object((("run_id", json_str(run.id)),)),
            ),
        )


def _tier_set(definition: WorkflowDefinitionRow | None, name: str) -> NotificationSetSpec | None:
    """Resolve a tier set against the run's stored workflow without raising."""
    if definition is None:
        return None
    try:
        workflow: CompiledWorkflow = compiled_from_json(parse_json(definition.compiled_json))
    except TelescreenError:
        return None
    return find_set(workflow.notification_sets, name)
