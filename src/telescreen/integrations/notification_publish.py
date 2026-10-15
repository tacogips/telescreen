"""Outbox-backed notification producer adapters with inline redaction."""

from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_null
from telescreen.core.vocabulary import (
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
    NotificationCategory,
)
from telescreen.integrations.notification_outbox import NotificationOutbox
from telescreen.masking.redact import RedactionResult, Redactor
from telescreen.ports.directory import OwnerDirectory, RecipientResolver
from telescreen.ports.directory_projection import DirectoryEntity
from telescreen.ports.notification_commands import (
    ContextFieldHits,
    DirectoryOperation,
    MemberSendOperation,
    NotificationCommand,
    NotificationOperation,
    SendOperation,
    notification_row_id,
    operation_seed,
)
from telescreen.ports.notification_types import NotificationContext, ProducerRedactionHits
from telescreen.ports.notifications import (
    MemberNotification,
    MemberNotifier,
    NotificationReceipt,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.workflow import TaskQueue, TaskSpec


@dataclass(frozen=True)
class NotificationOutboxPublisher:
    """Persist bounded notification commands and schedule their delivery."""

    outbox: NotificationOutbox
    queue: TaskQueue
    clock: Clock

    async def publish(
        self, session: AsyncSession, operations: tuple[NotificationOperation, ...]
    ) -> NotificationCommand:
        """Append one immutable command and enqueue its idempotent wakeup."""
        event_id: str = new_id()
        now: datetime = self.clock.now()
        command: NotificationCommand = NotificationCommand(event_id, now, operations)
        await self.outbox.append(session, command)
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.NOTIFICATION_COMMAND_DELIVERY,
                event_id,
                json_null(),
                now,
                f"notification-command:{event_id}",
            ),
        )
        return command

    async def publish_directory(
        self, session: AsyncSession, entities: tuple[DirectoryEntity, ...]
    ) -> tuple[NotificationCommand, ...]:
        """Split directory operations into the codec's maximum batch size."""
        commands: list[NotificationCommand] = []
        for start in range(0, len(entities), 8):
            ops: tuple[NotificationOperation, ...] = tuple(
                DirectoryOperation(entity) for entity in entities[start : start + 8]
            )
            commands.append(await self.publish(session, ops))
        return tuple(commands)


@dataclass(frozen=True)
class OutboxNotificationSender(NotificationSender):
    """Implement NotificationSender by committing redacted producer commands."""

    recipients: RecipientResolver
    redactor: Redactor
    publisher: NotificationOutboxPublisher

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Check recipients and persist only redacted content and aggregate hit counts."""
        category: NotificationCategory | None = request.category
        refs: tuple[str, ...] = request.recipients
        for ref in refs:
            category_filter: bool = category is not None and ref.startswith("member:")
            if not request.record_unreachable and not category_filter:
                await self.recipients.resolve(session, ref)
            else:
                try:
                    await self.recipients.resolve(session, ref)
                except TelescreenError as error:
                    if error.code is not ErrorCode.RECIPIENT_UNKNOWN:
                        raise
                    continue
        safe_subject: RedactionResult = self.redactor.redact(request.subject)
        safe_body: RedactionResult = self.redactor.redact(request.body)
        safe_context: NotificationContext
        context_hits: tuple[ContextFieldHits, ...]
        safe_context, context_hits = _redact_context(self.redactor, request.context)
        previous: ProducerRedactionHits = request.prior_hits or ProducerRedactionHits(0, 0)
        redaction: ProducerRedactionHits = _combine_hits(
            previous, safe_subject.hit_count, safe_body.hit_count, context_hits
        )
        operation: SendOperation = SendOperation(
            request.channel,
            refs,
            safe_subject.text,
            safe_body.text,
            request.actor_ref,
            request.run_id,
            request.issue_id,
            request.event,
            request.template_name,
            safe_context if request.context is not None else None,
            request.inline,
            request.set_name,
            request.target_index,
            request.record_unreachable,
            category,
            request.skip_unknown,
            redaction,
        )
        command: NotificationCommand = await self.publisher.publish(session, (operation,))
        ids: tuple[str, ...] = tuple(
            notification_row_id(operation_seed(command.event_id, 0), index)
            for index, _ in enumerate(refs)
        )
        return NotificationReceipt(ids, DlpVerdict.PASS, True, (), True)


@dataclass(frozen=True)
class OutboxMemberNotifier(MemberNotifier):
    """Implement MemberNotifier with a count-only redacted command."""

    owners: OwnerDirectory
    redactor: Redactor
    publisher: NotificationOutboxPublisher

    async def send_to_member(
        self, session: AsyncSession, notification: MemberNotification
    ) -> NotificationReceipt:
        """Check the member reference, redact context, and enqueue one command."""
        member_ref: str | None = await self.owners.member_ref(session, notification.member_id)
        if member_ref is None:
            raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")
        safe_context: NotificationContext
        context_hits: tuple[ContextFieldHits, ...]
        safe_context, context_hits = _redact_context(self.redactor, notification.context)
        operation: MemberSendOperation = MemberSendOperation(
            notification.member_id,
            notification.category,
            notification.event,
            safe_context,
            notification.issue_id,
            _combine_hits(
                notification.prior_hits or ProducerRedactionHits(0, 0), 0, 0, context_hits
            ),
        )
        command: NotificationCommand = await self.publisher.publish(session, (operation,))
        notification_id: str = notification_row_id(operation_seed(command.event_id, 0), 0)
        return NotificationReceipt((notification_id,), DlpVerdict.PASS, True, (), True)


def _redact_context(
    redactor: Redactor, context: NotificationContext | None
) -> tuple[NotificationContext, tuple[ContextFieldHits, ...]]:
    """Rebuild every string field after redaction, retaining only nonzero counts."""
    source: NotificationContext = context if context is not None else NotificationContext()
    issue_short_id: RedactionResult = redactor.redact(source.issue_short_id or "")
    issue_title: RedactionResult = redactor.redact(source.issue_title or "")
    issue_rule_class: RedactionResult = redactor.redact(source.issue_rule_class or "")
    issue_severity: RedactionResult = redactor.redact(source.issue_severity or "")
    issue_status: RedactionResult = redactor.redact(source.issue_status or "")
    issue_exposure: RedactionResult = redactor.redact(source.issue_exposure or "")
    issue_location: RedactionResult = redactor.redact(source.issue_location or "")
    issue_excerpt: RedactionResult = redactor.redact(source.issue_excerpt or "")
    issue_link: RedactionResult = redactor.redact(source.issue_link or "")
    run_workflow: RedactionResult = redactor.redact(source.run_workflow or "")
    step_name: RedactionResult = redactor.redact(source.step_name or "")
    task_title: RedactionResult = redactor.redact(source.task_title or "")
    task_due_at: RedactionResult = redactor.redact(source.task_due_at or "")
    task_link: RedactionResult = redactor.redact(source.task_link or "")
    assignee_ref: RedactionResult = redactor.redact(source.assignee_ref or "")
    digest_summary: RedactionResult = redactor.redact(source.digest_summary or "")
    health_target: RedactionResult = redactor.redact(source.health_target or "")
    health_condition: RedactionResult = redactor.redact(source.health_condition or "")
    health_details: RedactionResult = redactor.redact(source.health_details or "")
    alert_rule: RedactionResult = redactor.redact(source.alert_rule or "")
    alert_trigger: RedactionResult = redactor.redact(source.alert_trigger or "")
    alert_count: RedactionResult = redactor.redact(source.alert_count or "")
    alert_window: RedactionResult = redactor.redact(source.alert_window or "")
    activity_kind: RedactionResult = redactor.redact(source.activity_kind or "")
    activity_actor: RedactionResult = redactor.redact(source.activity_actor or "")
    comment_excerpt: RedactionResult = redactor.redact(source.comment_excerpt or "")
    report_summary: RedactionResult = redactor.redact(source.report_summary or "")
    report_assigned_open: RedactionResult = redactor.redact(source.report_assigned_open or "")
    data_short_id: RedactionResult = redactor.redact(source.data_short_id or "")
    data_name: RedactionResult = redactor.redact(source.data_name or "")
    data_locator: RedactionResult = redactor.redact(source.data_locator or "")
    data_deletion_due: RedactionResult = redactor.redact(source.data_deletion_due or "")
    data_link: RedactionResult = redactor.redact(source.data_link or "")
    fields: tuple[tuple[str, RedactionResult], ...] = (
        ("issue_short_id", issue_short_id),
        ("issue_title", issue_title),
        ("issue_rule_class", issue_rule_class),
        ("issue_severity", issue_severity),
        ("issue_status", issue_status),
        ("issue_exposure", issue_exposure),
        ("issue_location", issue_location),
        ("issue_excerpt", issue_excerpt),
        ("issue_link", issue_link),
        ("run_workflow", run_workflow),
        ("step_name", step_name),
        ("task_title", task_title),
        ("task_due_at", task_due_at),
        ("task_link", task_link),
        ("assignee_ref", assignee_ref),
        ("digest_summary", digest_summary),
        ("health_target", health_target),
        ("health_condition", health_condition),
        ("health_details", health_details),
        ("alert_rule", alert_rule),
        ("alert_trigger", alert_trigger),
        ("alert_count", alert_count),
        ("alert_window", alert_window),
        ("activity_kind", activity_kind),
        ("activity_actor", activity_actor),
        ("comment_excerpt", comment_excerpt),
        ("report_summary", report_summary),
        ("report_assigned_open", report_assigned_open),
        ("data_short_id", data_short_id),
        ("data_name", data_name),
        ("data_locator", data_locator),
        ("data_deletion_due", data_deletion_due),
        ("data_link", data_link),
    )
    safe: NotificationContext = replace(
        source,
        issue_short_id=issue_short_id.text if source.issue_short_id is not None else None,
        issue_title=issue_title.text if source.issue_title is not None else None,
        issue_rule_class=issue_rule_class.text if source.issue_rule_class is not None else None,
        issue_severity=issue_severity.text if source.issue_severity is not None else None,
        issue_status=issue_status.text if source.issue_status is not None else None,
        issue_exposure=issue_exposure.text if source.issue_exposure is not None else None,
        issue_location=issue_location.text if source.issue_location is not None else None,
        issue_excerpt=issue_excerpt.text if source.issue_excerpt is not None else None,
        issue_link=issue_link.text if source.issue_link is not None else None,
        run_workflow=run_workflow.text if source.run_workflow is not None else None,
        step_name=step_name.text if source.step_name is not None else None,
        task_title=task_title.text if source.task_title is not None else None,
        task_due_at=task_due_at.text if source.task_due_at is not None else None,
        task_link=task_link.text if source.task_link is not None else None,
        assignee_ref=assignee_ref.text if source.assignee_ref is not None else None,
        digest_summary=digest_summary.text if source.digest_summary is not None else None,
        health_target=health_target.text if source.health_target is not None else None,
        health_condition=health_condition.text if source.health_condition is not None else None,
        health_details=health_details.text if source.health_details is not None else None,
        alert_rule=alert_rule.text if source.alert_rule is not None else None,
        alert_trigger=alert_trigger.text if source.alert_trigger is not None else None,
        alert_count=alert_count.text if source.alert_count is not None else None,
        alert_window=alert_window.text if source.alert_window is not None else None,
        activity_kind=activity_kind.text if source.activity_kind is not None else None,
        activity_actor=activity_actor.text if source.activity_actor is not None else None,
        comment_excerpt=comment_excerpt.text if source.comment_excerpt is not None else None,
        report_summary=report_summary.text if source.report_summary is not None else None,
        report_assigned_open=report_assigned_open.text
        if source.report_assigned_open is not None
        else None,
        data_short_id=data_short_id.text if source.data_short_id is not None else None,
        data_name=data_name.text if source.data_name is not None else None,
        data_locator=data_locator.text if source.data_locator is not None else None,
        data_deletion_due=data_deletion_due.text if source.data_deletion_due is not None else None,
        data_link=data_link.text if source.data_link is not None else None,
    )
    hits: tuple[ContextFieldHits, ...] = tuple(
        ContextFieldHits(name, result.hit_count) for name, result in fields if result.hit_count > 0
    )
    return safe, hits


def _combine_hits(
    previous: ProducerRedactionHits,
    subject_hits: int,
    body_hits: int,
    context_hits: tuple[ContextFieldHits, ...],
) -> ProducerRedactionHits:
    """Add newly removed counts to count-only producer history by field."""
    counts: list[tuple[str, int]] = [(hit.field, hit.count) for hit in previous.context]
    for hit in context_hits:
        existing_index: int | None = next(
            (index for index, (name, _) in enumerate(counts) if name == hit.field), None
        )
        if existing_index is None:
            counts.append((hit.field, hit.count))
        else:
            existing_name: str
            existing_count: int
            existing_name, existing_count = counts[existing_index]
            counts[existing_index] = (existing_name, existing_count + hit.count)
    return ProducerRedactionHits(
        previous.subject + subject_hits,
        previous.body + body_hits,
        tuple(ContextFieldHits(name, count) for name, count in counts),
    )
