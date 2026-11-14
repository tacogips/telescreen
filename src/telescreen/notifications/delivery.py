"""Durable handler for at-least-once notification delivery tasks."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.json import json_int, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
    NotificationChannel,
    NotificationStatus,
)
from telescreen.db.models.notifications import NotificationRow
from telescreen.notifications.dlp import CensoredMessage, rehydrate
from telescreen.notifications.email import EmailNotifier
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.slack import SlackNotifier
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.collectors import RateLimited
from telescreen.ports.directory import RecipientResolver, ResolvedRecipients
from telescreen.ports.workflow import ClaimedTask, TaskResult


@dataclass(frozen=True)
class NotificationDeliveryHandler:
    """Deliver queued notifications and translate failures into retry outcomes."""

    sessions: async_sessionmaker[AsyncSession]
    resolver: RecipientResolver
    slack: SlackNotifier
    email: EmailNotifier
    audit: AuditLog
    clock: Clock
    repository: NotificationRepository
    kind: EngineTaskKind = EngineTaskKind.NOTIFICATION_DELIVERY

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Deliver one notification, skipping rows that have already completed."""
        async with self.sessions.begin() as session:
            row: NotificationRow | None = await self.repository.get(session, task.ref_id)
            if row is None or row.status != NotificationStatus.QUEUED.value:
                return TaskResult.done()
            resolved: ResolvedRecipients = await self.resolver.resolve(session, row.recipient_ref)
            message: CensoredMessage = rehydrate(row)
            attempt: int = max(row.delivery_attempts + 1, task.attempt)
            try:
                await self._send(row, resolved, message)
            except RateLimited as error:
                row.delivery_attempts = attempt
                delay: float = (
                    error.retry_after_seconds if error.retry_after_seconds is not None else 1.0
                )
                return TaskResult.retry(
                    self.clock.now() + timedelta(seconds=max(0.0, delay)),
                    ErrorCode.RATE_LIMITED,
                )
            except Exception:
                row.delivery_attempts = attempt
                if task.attempt >= task.max_attempts:
                    await self.repository.set_delivery(
                        session, row, NotificationStatus.FAILED, attempt, None
                    )
                    await self._audit(session, row, "notification.failed", attempt)
                    return TaskResult.dead(ErrorCode.DELIVERY_FAILED)
                delay_seconds: int = 30 * (2 ** max(0, task.attempt - 1))
                return TaskResult.retry(
                    self.clock.now() + timedelta(seconds=delay_seconds),
                    ErrorCode.DELIVERY_FAILED,
                )
            await self.repository.set_delivery(
                session, row, NotificationStatus.SENT, attempt, self.clock.now()
            )
            await self._audit(session, row, "notification.sent", attempt)
            return TaskResult.done()

    async def _send(
        self, row: NotificationRow, recipients: ResolvedRecipients, message: CensoredMessage
    ) -> None:
        """Send to each resolved endpoint for the row's channel."""
        channel: NotificationChannel = NotificationChannel(row.channel)
        if channel is NotificationChannel.SLACK:
            targets: tuple[str, ...] = recipients.slack_channels + recipients.slack_user_ids
            for target in targets:
                await self.slack.send(target, message)
            return
        for target in recipients.emails:
            await self.email.send(target, message)

    async def _audit(
        self, session: AsyncSession, row: NotificationRow, action: str, attempt: int
    ) -> None:
        """Record safe delivery metadata in the current transaction."""
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system",
                action,
                "notification",
                row.id,
                action,
                json_object(
                    (
                        ("channel", json_str(row.channel)),
                        ("verdict", json_str(DlpVerdict(row.dlp_verdict).value)),
                        ("attempt", json_int(attempt)),
                    )
                ),
            ),
        )
