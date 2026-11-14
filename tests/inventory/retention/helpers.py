"""Typed fakes and a publication/delivery harness for retention behavior tests."""

from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import (
    DataAssetState,
    DataFieldRole,
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
)
from telescreen.db.models import (
    DataAssetRow,
    DataFieldRow,
)
from telescreen.integrations.retention_delivery import RetentionDeliveryHandler
from telescreen.inventory.retention import DataOverdueHandler
from telescreen.inventory.retention_outbox import InventoryRetentionLookup, RetentionOutbox
from telescreen.masking.redact import Redactor
from telescreen.notifications.retention import RetentionNotificationReceiver
from telescreen.ports.asset_context import AssetOwnerLookup
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import AssigneeIds, RecipientResolver, ResolvedRecipients
from telescreen.ports.notifications import (
    MemberNotification,
    MemberNotifier,
    NotificationReceipt,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.workflow import ClaimedTask, TaskQueue, TaskResult, TaskSpec


@dataclass
class RecordingAudit(AuditLog):
    """Capture overdue audit records."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one audit entry."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class FakeRecipients(RecipientResolver):
    """Resolve current member and team refs and reject stale refs."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Return an empty delivery-address projection."""
        del session, ref
        return ResolvedRecipients((), (), (), ())

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Return false for unused membership checks."""
        del session, member_id, ref
        return False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Return the active member or reject a stale reference."""
        del session
        if ref == "member:active":
            return AssigneeIds("member-id", None)
        if ref == "team:ops":
            return AssigneeIds(None, "team-id")
        from telescreen.core.errors import TelescreenError

        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")


@dataclass
class FakeOwners(AssetOwnerLookup):
    """Supply a configured fallback reference."""

    fallback: str | None
    calls: int = 0

    async def fallback_owner(
        self, source: str, resource_key: str, display_locator: str
    ) -> str | None:
        """Return the configured fallback and record that it was consulted."""
        del source, resource_key, display_locator
        self.calls += 1
        return self.fallback


@dataclass
class FakeMemberNotifier(MemberNotifier):
    """Capture member notification deliveries."""

    sent: list[MemberNotification] = field(default_factory=list)

    async def send_to_member(
        self, session: AsyncSession, notification: MemberNotification
    ) -> NotificationReceipt:
        """Record one member notification."""
        del session
        self.sent.append(notification)
        return NotificationReceipt(("notification-1",), DlpVerdict.PASS, True)


@dataclass
class FailingMemberNotifier(FakeMemberNotifier):
    """Record every attempt and fail for one selected data short id."""

    failed_data_short_id: str = "DATA-1"
    attempts: list[str] = field(default_factory=list)

    async def send_to_member(
        self, session: AsyncSession, notification: MemberNotification
    ) -> NotificationReceipt:
        """Raise after recording the configured failed delivery attempt."""
        data_short_id: str | None = notification.context.data_short_id
        assert data_short_id is not None
        self.attempts.append(data_short_id)
        if data_short_id == self.failed_data_short_id:
            raise RuntimeError("injected member send failure")
        return await super().send_to_member(session, notification)


@dataclass
class FakeSender(NotificationSender):
    """Capture team channel notification deliveries."""

    sent: list[NotificationRequest] = field(default_factory=list)

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Record one sender request."""
        del session
        self.sent.append(request)
        return NotificationReceipt((f"notification-{len(self.sent)}",), DlpVerdict.PASS, True)


@dataclass
class FakeQueue(TaskQueue):
    """Capture scheduled tasks."""

    tasks: list[TaskSpec] = field(default_factory=list)

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Record one idempotent task."""
        del session
        self.tasks.append(spec)
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Report that no task was cancelled."""
        del session, ref_id
        return 0


def _field(field_id: str, key: str, role: DataFieldRole, field_type: str) -> DataFieldRow:
    """Build a persisted metadata field."""
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    return DataFieldRow(
        id=field_id,
        key=key,
        label=key,
        description=None,
        field_type=field_type,
        options_json=None,
        required=False,
        position=0,
        role=role.value,
        archived_at=None,
        created_by="member:admin",
        created_at=now,
        updated_at=now,
    )


def _asset(asset_id: str, number: int, now: datetime) -> DataAssetRow:
    """Build one due present asset."""
    return DataAssetRow(
        id=asset_id,
        number=number,
        resource_key=f"{number:064x}",
        source="manual",
        origin="registered",
        kind="file",
        state=DataAssetState.PRESENT.value,
        name="Asset",
        display_locator="https://example.test",
        collector_config_id=None,
        sweep_covered=False,
        item_count=None,
        first_seen_at=now,
        last_seen_at=now,
        deleted_at=None,
        overdue_notified_for=None,
        created_by="member:admin",
        created_at=now,
        updated_at=now,
    )


@dataclass(frozen=True)
class RetentionRuntime:
    """Exercise the production publisher and receiver with independently committed handoff."""

    sessions: async_sessionmaker[AsyncSession]
    members: MemberNotifier
    sender: NotificationSender
    recipients: RecipientResolver
    owners: AssetOwnerLookup
    audit: AuditLog
    clock: FakeClock
    public_base_url: str | None

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Publish first, then attempt every pending delivery without coupling rollbacks."""
        await DataOverdueHandler(
            self.sessions, Redactor.default(), self.clock, self.public_base_url
        ).handle(task)
        outbox: RetentionOutbox = RetentionOutbox()
        receiver: RetentionNotificationReceiver = RetentionNotificationReceiver(
            self.sessions,
            InventoryRetentionLookup(self.sessions, self.clock),
            self.members,
            self.sender,
            self.recipients,
            self.owners,
            self.audit,
            self.clock,
        )
        delivery: RetentionDeliveryHandler = RetentionDeliveryHandler(
            self.sessions, outbox, receiver, self.clock
        )
        async with self.sessions() as session:
            identifiers: tuple[str, ...] = await outbox.pending_ids(session, 10000)
        for event_id in identifiers:
            with suppress(RuntimeError):
                await delivery.handle(
                    replace(task, kind=EngineTaskKind.RETENTION_DELIVERY, ref_id=event_id)
                )
        return TaskResult.done()
