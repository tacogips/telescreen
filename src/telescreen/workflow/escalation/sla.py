"""Durable human-task SLA escalation handler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.json import json_object, json_str, parse_json
from telescreen.core.vocabulary import (
    ActorKind,
    EngineTaskKind,
    IssueHistoryKind,
    NotificationChannel,
)
from telescreen.db.models import EscalationPolicyRow, HumanTaskRow, WorkflowRunRow
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import RecipientResolver
from telescreen.ports.issues import IssueHistoryEntry, IssueReader, IssueRecorder, IssueSnapshot
from telescreen.ports.notifications import NotificationRequest, NotificationSender
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskSpec
from telescreen.workflow.dsl.escalation import (
    EscalationPolicySpec,
    EscalationTier,
    escalation_from_json,
)
from telescreen.workflow.engine.repository import WorkflowRepository
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
            await self._send_tier(session, human_task, run, tier.notify, tier.channels)
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
        refs: tuple[str, ...],
        channels: tuple[NotificationChannel, ...],
    ) -> None:
        """Enqueue DLP-gated tier notifications by recipient reference per channel.

        References are passed through unresolved (the sender resolves them at delivery);
        unknown recipients are skipped so the escalation itself still completes.
        """
        reachable: tuple[ReachableRef, ...] = await reachable_refs(self.resolver, session, refs)
        for channel in channels:
            channel_refs: tuple[str, ...] = refs_for_channel(reachable, channel)
            if channel_refs:
                await self.notifications.send(
                    session,
                    NotificationRequest(
                        channel,
                        channel_refs,
                        "Workflow task escalated",
                        human_task.title,
                        "system:workflow",
                        run.id,
                        run.issue_id,
                    ),
                )

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
