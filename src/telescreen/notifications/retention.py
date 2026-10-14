"""Notification-owned retention consumption, recipient policy and deduplication."""

from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_int, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    ErrorCode,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.db.models import TeamRow
from telescreen.db.models.retention_handoff import RetentionNotificationReceiptRow
from telescreen.ports.asset_context import AssetOwnerLookup
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import AssigneeIds, RecipientResolver
from telescreen.ports.notifications import (
    MemberNotification,
    MemberNotifier,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.retention import (
    RetentionCommand,
    RetentionLookup,
    RetentionReceipt,
    RetentionStatus,
    encode_retention_command,
)


@dataclass(frozen=True)
class RetentionNotificationReceiver:
    """Commit receipt, policy outcomes, notification intents and audit together."""

    sessions: async_sessionmaker[AsyncSession]
    inventory: RetentionLookup
    members: MemberNotifier
    sender: NotificationSender
    recipients: RecipientResolver
    owners: AssetOwnerLookup
    audit: AuditLog
    clock: Clock

    async def receive(self, command: RetentionCommand) -> RetentionReceipt:
        """Acknowledge only after notification intents are durably committed."""
        digest: str = sha256(encode_retention_command(command).encode("utf-8")).hexdigest()
        # An already committed receipt can be acknowledged even while inventory is down.
        async with self.sessions() as session:
            previous: RetentionNotificationReceiptRow | None = await session.get(
                RetentionNotificationReceiptRow, command.event_id
            )
            if previous is not None:
                self._check_digest(previous, digest)
                return RetentionReceipt.DUPLICATE
        status: RetentionStatus = await self.inventory.status(command)
        async with self.sessions.begin() as session:
            if not await self._claim(session, command.event_id, digest):
                previous = await session.get(RetentionNotificationReceiptRow, command.event_id)
                if previous is None:
                    raise TelescreenError(ErrorCode.CONFLICT, "retention receipt unavailable")
                self._check_digest(previous, digest)
                result: RetentionReceipt = RetentionReceipt.DUPLICATE
            else:
                if status.current:
                    await self._notify(session, command, status.owner_ref)
                    result = RetentionReceipt.APPLIED
                else:
                    await self.audit.append(
                        session,
                        AuditEntry(
                            ActorKind.SYSTEM,
                            "system:data-overdue",
                            "data_asset.overdue_superseded",
                            "data_asset",
                            command.asset_id,
                            "Superseded overdue obligation skipped",
                            json_object((("obligation_id", json_str(command.event_id)),)),
                        ),
                    )
                    result = RetentionReceipt.SUPERSEDED
        return result

    async def _claim(self, session: AsyncSession, event_id: str, digest: str) -> bool:
        """Use transactional DML rather than a savepoint that can commit early in SQLite."""
        if session.get_bind().dialect.name == "postgresql":
            return (
                await session.scalar(
                    pg_insert(RetentionNotificationReceiptRow)
                    .values(
                        id=event_id, payload_sha256=digest, applied_at=ensure_utc(self.clock.now())
                    )
                    .on_conflict_do_nothing(index_elements=("id",))
                    .returning(RetentionNotificationReceiptRow.id)
                )
            ) is not None
        if session.get_bind().dialect.name == "sqlite":
            return (
                await session.scalar(
                    sqlite_insert(RetentionNotificationReceiptRow)
                    .values(
                        id=event_id, payload_sha256=digest, applied_at=ensure_utc(self.clock.now())
                    )
                    .on_conflict_do_nothing(index_elements=("id",))
                    .returning(RetentionNotificationReceiptRow.id)
                )
            ) is not None
        raise TelescreenError(ErrorCode.CONFIG_ERROR, "unsupported retention receipt database")

    def _check_digest(self, receipt: RetentionNotificationReceiptRow, digest: str) -> None:
        """Never treat a reused identity carrying a different payload as a duplicate."""
        if receipt.payload_sha256 != digest:
            raise TelescreenError(ErrorCode.CONFLICT, "retention event identity conflict")

    async def _assignee(self, session: AsyncSession, ref: str | None) -> AssigneeIds | None:
        """Only stale or invalid references fall back; infrastructure failures retry."""
        if ref is None:
            return None
        kind: str
        separator: str
        key: str
        kind, separator, key = ref.partition(":")
        if not separator or not key or kind not in ("member", "team"):
            return None
        try:
            return await self.recipients.assignee_of(session, ref)
        except TelescreenError as error:
            if error.code is ErrorCode.RECIPIENT_UNKNOWN:
                return None
            raise

    async def _notify(
        self, session: AsyncSession, command: RetentionCommand, owner_ref: str | None
    ) -> None:
        """Resolve current identities and route through existing preference and DLP services."""
        assignee: AssigneeIds | None = await self._assignee(session, owner_ref)
        if assignee is None:
            fallback: str | None = await self.owners.fallback_owner(
                command.source, command.resource_key, command.context.data_locator or ""
            )
            assignee = await self._assignee(session, fallback)
        count: int = 0
        recipient_kind: str = "none"
        if assignee is not None and assignee.member_id is not None:
            recipient_kind = "member"
            await self.members.send_to_member(
                session,
                MemberNotification(
                    assignee.member_id,
                    NotificationCategory.ASSIGNED,
                    NotificationEvent.DATA_OVERDUE,
                    command.context,
                ),
            )
            count = 1
        elif assignee is not None and assignee.team_id is not None:
            team: TeamRow | None = await session.get(TeamRow, assignee.team_id)
            if team is not None:
                recipient_kind = "team"
                for channel in (NotificationChannel.EMAIL, NotificationChannel.SLACK):
                    await self.sender.send(
                        session,
                        NotificationRequest(
                            channel,
                            (f"team:{team.slug}",),
                            "",
                            "",
                            "system:data-overdue",
                            event=NotificationEvent.DATA_OVERDUE,
                            template_name="system.data_overdue",
                            context=command.context,
                            record_unreachable=True,
                        ),
                    )
                    count += 1
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:data-overdue",
                "data_asset.overdue_notified",
                "data_asset",
                command.asset_id,
                "Overdue data notification recorded",
                json_object(
                    (
                        ("recipient_kind", json_str(recipient_kind)),
                        ("notification_count", json_int(count)),
                        ("obligation_id", json_str(command.event_id)),
                    )
                ),
            ),
        )
