"""Idempotent notification command inbox with replica-aware recipient deferral."""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from hashlib import sha256

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import Insert

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, NotificationChannel
from telescreen.db.models import NotificationInboxRow, RetentionNotificationReceiptRow
from telescreen.notifications.member_send import MemberReferences
from telescreen.notifications.sender import NotificationService
from telescreen.ports.directory import RecipientResolver
from telescreen.ports.notification_commands import (
    DirectoryOperation,
    MemberSendOperation,
    NotificationCommand,
    NotificationCommandReceipt,
    SendOperation,
    encode_notification_command,
    operation_seed,
)
from telescreen.ports.notifications import MemberNotification, NotificationRequest
from telescreen.replica.store import DirectoryReplica


class Producer(StrEnum):
    """Authenticated service that sent a notification command."""

    TELESCREEN = "telescreen"
    INVENTORY = "inventory"


@dataclass(frozen=True)
class NotificationInbox:
    """Validate, deduplicate, defer and atomically apply producer commands."""

    sessions: async_sessionmaker[AsyncSession]
    service: NotificationService
    recipients: RecipientResolver
    replica: DirectoryReplica
    clock: Clock
    grace_seconds: int
    member_references: MemberReferences | None = None

    async def receive(
        self, command: NotificationCommand, producer: Producer
    ) -> NotificationCommandReceipt:
        """Apply one command, acknowledging duplicates and deferring unknown recipients."""
        self._authorize(command, producer)
        payload_hash: str = sha256(encode_notification_command(command).encode("utf-8")).hexdigest()
        async with self.sessions() as session:
            previous: NotificationInboxRow | None = await session.get(
                NotificationInboxRow, command.event_id
            )
            retention: RetentionNotificationReceiptRow | None = (
                await session.get(RetentionNotificationReceiptRow, command.event_id)
                if producer is Producer.INVENTORY
                else None
            )
            if previous is not None:
                if previous.payload_sha256 != payload_hash:
                    raise TelescreenError(
                        ErrorCode.CONFLICT, "notification event identity conflict"
                    )
                return NotificationCommandReceipt.DUPLICATE
            if retention is not None:
                return NotificationCommandReceipt.DUPLICATE
            if await self._should_defer(session, command):
                return NotificationCommandReceipt.DEFERRED
        async with self.sessions.begin() as session:
            if not await self._claim(session, command.event_id, payload_hash):
                previous = await session.get(NotificationInboxRow, command.event_id)
                if previous is None or previous.payload_sha256 != payload_hash:
                    raise TelescreenError(
                        ErrorCode.CONFLICT, "notification event identity conflict"
                    )
                return NotificationCommandReceipt.DUPLICATE
            for index, operation in enumerate(command.operations):
                if isinstance(operation, DirectoryOperation):
                    await self.replica.apply(session, operation.entity)
                elif isinstance(operation, MemberSendOperation):
                    try:
                        await self.service.send_to_member(
                            session,
                            MemberNotification(
                                operation.member_id,
                                operation.category,
                                operation.event,
                                operation.context,
                                operation.issue_id,
                                operation_seed(command.event_id, index),
                                operation.redaction,
                            ),
                        )
                    except TelescreenError as error:
                        if error.code is not ErrorCode.RECIPIENT_UNKNOWN:
                            raise
                        await self.service.send(
                            session,
                            NotificationRequest(
                                NotificationChannel.EMAIL,
                                (f"member:{operation.member_id}",),
                                "",
                                "",
                                "system:member-notification",
                                issue_id=operation.issue_id,
                                event=operation.event,
                                context=operation.context,
                                record_unreachable=True,
                                category=operation.category,
                                id_seed=operation_seed(command.event_id, index),
                                prior_hits=operation.redaction,
                                skip_unknown=True,
                            ),
                        )
                else:
                    await self.service.send(session, self._request(command, operation, index))
        return NotificationCommandReceipt.APPLIED

    def _authorize(self, command: NotificationCommand, producer: Producer) -> None:
        """Restrict inventory to overdue notification sends only."""
        if producer is Producer.TELESCREEN:
            return
        if any(
            isinstance(operation, DirectoryOperation)
            or isinstance(operation, SendOperation)
            and operation.event is None
            or isinstance(operation, MemberSendOperation)
            and operation.event.value != "data_overdue"
            or isinstance(operation, SendOperation)
            and operation.event is not None
            and operation.event.value != "data_overdue"
            for operation in command.operations
        ):
            raise TelescreenError(ErrorCode.FORBIDDEN, "notification operation is not permitted")

    async def _should_defer(self, session: AsyncSession, command: NotificationCommand) -> bool:
        """Check all recipients without writing any state inside the grace interval."""
        age: timedelta = self.clock.now() - command.occurred_at
        if age >= timedelta(seconds=self.grace_seconds):
            return False
        for operation in command.operations:
            if isinstance(operation, MemberSendOperation) and self.member_references is not None:
                try:
                    await self.member_references.member_reference(session, operation.member_id)
                except TelescreenError as error:
                    if error.code is ErrorCode.RECIPIENT_UNKNOWN:
                        return True
                    raise
                continue
            refs: tuple[str, ...] = (
                operation.recipients
                if isinstance(operation, SendOperation)
                else (f"member:{operation.member_id}",)
                if isinstance(operation, MemberSendOperation)
                else ()
            )
            for ref in refs:
                try:
                    await self.recipients.resolve(session, ref)
                except TelescreenError as error:
                    if error.code is ErrorCode.RECIPIENT_UNKNOWN:
                        return True
                    raise
        return False

    async def _claim(self, session: AsyncSession, event_id: str, digest: str) -> bool:
        """Claim the command id in the current transaction using dialect-native DML."""
        model: type[NotificationInboxRow] = NotificationInboxRow
        if session.get_bind().dialect.name == "postgresql":
            statement: Insert = (
                pg_insert(model)
                .values(id=event_id, payload_sha256=digest, applied_at=ensure_utc(self.clock.now()))
                .on_conflict_do_nothing(index_elements=("id",))
                .returning(model.id)
            )
        elif session.get_bind().dialect.name == "sqlite":
            statement = (
                sqlite_insert(model)
                .values(id=event_id, payload_sha256=digest, applied_at=ensure_utc(self.clock.now()))
                .on_conflict_do_nothing(index_elements=("id",))
                .returning(model.id)
            )
        else:
            raise TelescreenError(ErrorCode.CONFIG_ERROR, "unsupported notification database")
        claimed: str | None = await session.scalar(statement)
        return claimed is not None

    def _request(
        self, command: NotificationCommand, operation: SendOperation, index: int
    ) -> NotificationRequest:
        """Map a send operation to the existing notification service contract."""
        return NotificationRequest(
            operation.channel,
            operation.recipients,
            operation.subject,
            operation.body,
            operation.actor_ref,
            run_id=operation.run_id,
            issue_id=operation.issue_id,
            event=operation.event,
            template_name=operation.template_name,
            context=operation.context,
            inline=operation.inline,
            set_name=operation.set_name,
            target_index=operation.target_index,
            record_unreachable=operation.record_unreachable,
            category=operation.category,
            id_seed=operation_seed(command.event_id, index),
            prior_hits=operation.redaction,
            skip_unknown=True,
        )
