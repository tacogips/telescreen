"""Transaction-bound DLP, audit, and notification task enqueue service."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.core.json import json_int, json_null, json_object, json_str
from telescreen.core.vocabulary import ActorKind, DlpVerdict, EngineTaskKind, NotificationStatus
from telescreen.db.models.notifications import NotificationRow
from telescreen.notifications.dlp import CensoredMessage, DlpGate, OutboundMessage
from telescreen.notifications.repository import NotificationRepository
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import RecipientResolver
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.ports.workflow import TaskQueue, TaskSpec


@dataclass(frozen=True)
class NotificationService:
    """Gate, persist, audit, and enqueue notifications in one transaction."""

    gate: DlpGate
    resolver: RecipientResolver
    queue: TaskQueue
    audit: AuditLog
    clock: Clock
    repository: NotificationRepository

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Validate recipients and enqueue safe per-recipient notification rows."""
        for recipient_ref in request.recipients:
            await self.resolver.resolve(session, recipient_ref)
        safe_message: CensoredMessage = await self.gate.inspect(
            OutboundMessage(request.subject, request.body)
        )
        notification_ids: list[str] = []
        for recipient_ref in request.recipients:
            notification_id: str = new_id()
            notification_ids.append(notification_id)
            status: NotificationStatus = (
                NotificationStatus.BLOCKED
                if safe_message.verdict is DlpVerdict.BLOCKED
                else NotificationStatus.QUEUED
            )
            row: NotificationRow = NotificationRow(
                id=notification_id,
                channel=request.channel.value,
                recipient_ref=recipient_ref,
                subject=safe_message.subject,
                body=safe_message.body,
                dlp_verdict=safe_message.verdict.value,
                dlp_hit_count=safe_message.hit_count,
                status=status.value,
                delivery_attempts=0,
                run_id=request.run_id,
                issue_id=request.issue_id,
                created_at=self.clock.now(),
                sent_at=None,
            )
            await self.repository.insert(session, row)
            action: str = (
                "notification.blocked"
                if status is NotificationStatus.BLOCKED
                else "notification.queued"
            )
            await self.audit.append(
                session,
                AuditEntry(
                    ActorKind.SYSTEM,
                    request.actor_ref,
                    action,
                    "notification",
                    notification_id,
                    action,
                    json_object(
                        (
                            ("channel", json_str(request.channel.value)),
                            ("verdict", json_str(safe_message.verdict.value)),
                            ("hit_count", json_int(safe_message.hit_count)),
                        )
                    ),
                ),
            )
            if status is NotificationStatus.QUEUED:
                await self.queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.NOTIFICATION_DELIVERY,
                        notification_id,
                        json_null(),
                        self.clock.now(),
                        f"notify:{notification_id}",
                        5,
                    ),
                )
        return NotificationReceipt(
            tuple(notification_ids),
            safe_message.verdict,
            bool(notification_ids) and safe_message.verdict is not DlpVerdict.BLOCKED,
        )
