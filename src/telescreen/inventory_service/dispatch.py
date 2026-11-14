"""Inventory-local eligibility checks and overdue notification publication."""

import logging
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.audit.service import AuditLogService
from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_int, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    ErrorCode,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.inventory.retention_outbox import RetentionOutbox, retention_status
from telescreen.inventory_service.lookups import ReplicaAssetOwnerLookup
from telescreen.inventory_service.notify_outbox import NotificationOutbox
from telescreen.masking.redact import RedactionResult, Redactor
from telescreen.ports.audit_types import AuditEntry
from telescreen.ports.directory import AssigneeIds
from telescreen.ports.notification_commands import (
    MemberSendOperation,
    NotificationCommand,
    SendOperation,
)
from telescreen.ports.notification_types import (
    ContextFieldHits,
    NotificationContext,
    ProducerRedactionHits,
)
from telescreen.ports.retention import RetentionCommand, RetentionStatus
from telescreen.replica.directory import ReplicaOwnerDirectory, ReplicaRecipientResolver

logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class RetentionDispatcher:
    """Dispatch overdue obligations with eligibility, audit and ack in one transaction."""

    sessions: async_sessionmaker[AsyncSession]
    outbox: RetentionOutbox
    notifications: NotificationOutbox
    recipients: ReplicaRecipientResolver
    owners: ReplicaOwnerDirectory
    fallback: ReplicaAssetOwnerLookup
    redactor: Redactor
    audit: AuditLogService
    clock: Clock
    after_id: str | None = None
    batch_size: int = 20

    async def tick(self) -> None:
        """Process a rotating batch; failed obligations remain pending."""
        async with self.sessions() as session:
            identifiers: tuple[str, ...] = await self.outbox.pending_ids(
                session, self.batch_size, self.after_id
            )
            if not identifiers and self.after_id is not None:
                self.after_id = None
                identifiers = await self.outbox.pending_ids(session, self.batch_size)
        for event_id in identifiers:
            self.after_id = event_id
            try:
                async with self.sessions.begin() as session:
                    pending: RetentionCommand | None = await self.outbox.get_pending(
                        session, event_id
                    )
                    if pending is None:
                        continue
                    command: RetentionCommand = pending
                    eligible: RetentionStatus = await retention_status(session, command, self.clock)
                    if not eligible.current:
                        await self.audit.append(
                            session,
                            _audit_entry(
                                command,
                                "data_asset.overdue_superseded",
                                "Superseded overdue obligation skipped",
                                (("obligation_id", json_str(command.event_id)),),
                            ),
                        )
                    else:
                        await self._publish(session, command, eligible.owner_ref)
                    await self.outbox.acknowledge(session, event_id, self.clock.now())
            except Exception as error:
                logger.warning("inventory retention dispatch failed type=%s", type(error).__name__)

    async def _publish(
        self, session: AsyncSession, command: RetentionCommand, owner_ref: str | None
    ) -> None:
        """Resolve recipients, redact context, and persist command plus audit atomically."""
        assignee: AssigneeIds | None = await _resolve_assignee(session, self.recipients, owner_ref)
        if assignee is None:
            fallback_ref: str | None = await self.fallback.fallback_owner_in_session(
                session,
                command.source,
                command.resource_key,
                command.context.data_locator or "",
            )
            assignee = await _resolve_assignee(session, self.recipients, fallback_ref)
        context: NotificationContext
        redaction: ProducerRedactionHits
        context, redaction = _redact_context(command.context, self.redactor)
        operations: tuple[MemberSendOperation | SendOperation, ...]
        recipient_kind: str
        if assignee is not None and assignee.member_id is not None:
            recipient_kind = "member"
            operations = (
                MemberSendOperation(
                    assignee.member_id,
                    NotificationCategory.ASSIGNED,
                    NotificationEvent.DATA_OVERDUE,
                    context,
                    None,
                    redaction,
                ),
            )
        elif assignee is not None and assignee.team_id is not None:
            recipient_kind = "team"
            team_ref: str | None = await self.owners.team_ref(session, assignee.team_id)
            if team_ref is None:
                recipient_kind = "none"
                operations = ()
            else:
                operations = tuple(
                    SendOperation(
                        channel,
                        (team_ref,),
                        "",
                        "",
                        "system:data-overdue",
                        None,
                        None,
                        NotificationEvent.DATA_OVERDUE,
                        "system.data_overdue",
                        context,
                        False,
                        None,
                        None,
                        True,
                        None,
                        False,
                        redaction,
                    )
                    for channel in (NotificationChannel.EMAIL, NotificationChannel.SLACK)
                )
        else:
            recipient_kind = "none"
            operations = ()
        if operations:
            notification: NotificationCommand = NotificationCommand(
                command.event_id, ensure_utc(self.clock.now()), operations
            )
            await self.notifications.append(session, notification)
        await self.audit.append(
            session,
            _audit_entry(
                command,
                "data_asset.overdue_notified",
                "Overdue data notification recorded",
                (
                    ("recipient_kind", json_str(recipient_kind)),
                    ("notification_count", json_int(len(operations))),
                    ("obligation_id", json_str(command.event_id)),
                ),
            ),
        )


async def _resolve_assignee(
    session: AsyncSession, recipients: ReplicaRecipientResolver, ref: str | None
) -> AssigneeIds | None:
    """Treat stale recipient references as fallback candidates only."""
    if ref is None:
        return None
    try:
        return await recipients.assignee_of(session, ref)
    except TelescreenError as error:
        if error.code is ErrorCode.RECIPIENT_UNKNOWN:
            return None
        raise


def _redact_context(
    context: NotificationContext, redactor: Redactor
) -> tuple[NotificationContext, ProducerRedactionHits]:
    """Redact retention strings and retain only per-field hit counts."""
    short_id: RedactionResult | None = (
        redactor.redact(context.data_short_id) if context.data_short_id else None
    )
    name: RedactionResult | None = redactor.redact(context.data_name) if context.data_name else None
    locator: RedactionResult | None = (
        redactor.redact(context.data_locator) if context.data_locator else None
    )
    due: RedactionResult | None = (
        redactor.redact(context.data_deletion_due) if context.data_deletion_due else None
    )
    link: RedactionResult | None = redactor.redact(context.data_link) if context.data_link else None
    sanitized: NotificationContext = replace(
        context,
        data_short_id=short_id.text if short_id else None,
        data_name=name.text if name else None,
        data_locator=locator.text if locator else None,
        data_deletion_due=due.text if due else None,
        data_link=link.text if link else None,
    )
    hits: tuple[ContextFieldHits, ...] = tuple(
        ContextFieldHits(field, result.hit_count)
        for field, result in (
            ("data_short_id", short_id),
            ("data_name", name),
            ("data_locator", locator),
            ("data_deletion_due", due),
            ("data_link", link),
        )
        if result is not None and result.hit_count > 0
    )
    return sanitized, ProducerRedactionHits(0, 0, tuple(hits))


def _audit_entry(
    command: RetentionCommand,
    action: str,
    summary: str,
    details: tuple[tuple[str, JsonValue], ...],
) -> AuditEntry:
    """Create the safe retention audit record for the local chain."""
    return AuditEntry(
        ActorKind.SYSTEM,
        "system:data-overdue",
        action,
        "data_asset",
        command.asset_id,
        summary,
        json_object(details),
    )
