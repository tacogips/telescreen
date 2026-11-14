"""Notification inbox receipt and unknown-recipient grace behavior."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    DlpPolicy,
    ErrorCode,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
    NotificationStatus,
)
from telescreen.db.models import (
    NotificationDirectoryEntryRow,
    NotificationInboxRow,
    NotificationRow,
    RetentionNotificationReceiptRow,
)
from telescreen.db.ownership import Store
from telescreen.notification_service.inbox import NotificationInbox, Producer
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.ports.directory import ResolvedRecipients
from telescreen.ports.directory_projection import (
    DirectoryEntity,
    DirectoryEntityKind,
    MemberEntity,
)
from telescreen.ports.notification_commands import (
    DirectoryOperation,
    MemberSendOperation,
    NotificationCommand,
    NotificationCommandReceipt,
    NotificationOperation,
    ProducerRedactionHits,
    SendOperation,
    notification_row_id,
)
from telescreen.ports.notifications import NotificationContext
from telescreen.replica.store import DirectoryReplica
from tests.notifications.v12.fakes import make_gate
from tests.notifications.v13.fakes import RecordingAudit, RecordingQueue, StaticResolver
from tests.support.db import SqliteHarness, make_sqlite_harness


@pytest.fixture
async def inbox_world() -> tuple[SqliteHarness, FakeClock, NotificationInbox]:
    """Build an isolated Notification store with an empty recipient replica."""
    harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    clock: FakeClock = FakeClock(datetime(2026, 10, 16, tzinfo=UTC))
    team_recipients: ResolvedRecipients = ResolvedRecipients(("team@example.test",), (), (), ())
    member_recipients: ResolvedRecipients = ResolvedRecipients(("alice@example.test",), (), (), ())
    resolver: StaticResolver = StaticResolver(
        (("team:ops", team_recipients), ("member:alice", member_recipients))
    )
    queue: RecordingQueue = RecordingQueue()
    audit: RecordingAudit = RecordingAudit()
    service: NotificationService = NotificationService(
        make_gate(DlpPolicy.MASK),
        resolver,
        queue,
        audit,
        clock,
        NotificationRepository(),
    )
    inbox: NotificationInbox = NotificationInbox(
        harness.sessions,
        service,
        resolver,
        DirectoryReplica(NotificationDirectoryEntryRow),
        clock,
        600,
    )
    return harness, clock, inbox


def _overdue_command(occurred_at: datetime) -> NotificationCommand:
    """Return one safe inventory data-overdue send operation."""
    operation: SendOperation = SendOperation(
        NotificationChannel.EMAIL,
        ("member:missing",),
        "",
        "",
        "system:data-overdue",
        None,
        None,
        NotificationEvent.DATA_OVERDUE,
        None,
        None,
        False,
        None,
        None,
        True,
        None,
        True,
        ProducerRedactionHits(0, 0),
    )
    return NotificationCommand("event-overdue-1", occurred_at, (operation,))


async def test_unknown_recipient_inside_grace_is_write_free(
    inbox_world: tuple[SqliteHarness, FakeClock, NotificationInbox],
) -> None:
    """Defer before claiming a receipt or writing any notification row."""
    harness: SqliteHarness
    clock: FakeClock
    inbox: NotificationInbox
    harness, clock, inbox = inbox_world
    command: NotificationCommand = _overdue_command(clock.now())
    result: NotificationCommandReceipt = await inbox.receive(command, Producer.INVENTORY)
    assert result is NotificationCommandReceipt.DEFERRED
    async with harness.sessions() as session:
        inbox_count: int = (
            await session.scalar(select(func.count()).select_from(NotificationInboxRow)) or 0
        )
        row_count: int = (
            await session.scalar(select(func.count()).select_from(NotificationRow)) or 0
        )
    assert inbox_count == row_count == 0
    await harness.close()


async def test_unknown_recipient_after_grace_is_recorded_as_skipped(
    inbox_world: tuple[SqliteHarness, FakeClock, NotificationInbox],
) -> None:
    """Apply an overdue command after grace as one safe skipped notification."""
    harness: SqliteHarness
    clock: FakeClock
    inbox: NotificationInbox
    harness, clock, inbox = inbox_world
    command: NotificationCommand = _overdue_command(clock.now() - timedelta(seconds=601))
    result: NotificationCommandReceipt = await inbox.receive(command, Producer.INVENTORY)
    assert result is NotificationCommandReceipt.APPLIED
    async with harness.sessions() as session:
        row: NotificationRow | None = await session.scalar(select(NotificationRow))
        receipt: NotificationInboxRow | None = await session.get(
            NotificationInboxRow, command.event_id
        )
    assert receipt is not None
    assert row is not None
    assert row.status == NotificationStatus.SKIPPED.value
    assert row.error_code == "recipient_unknown"
    assert row.id == notification_row_id("event-overdue-1:0", 0)
    duplicate: NotificationCommandReceipt = await inbox.receive(command, Producer.INVENTORY)
    assert duplicate is NotificationCommandReceipt.DUPLICATE
    changed: NotificationCommand = NotificationCommand(
        command.event_id,
        command.occurred_at,
        (
            SendOperation(
                NotificationChannel.EMAIL,
                ("member:missing",),
                "different",
                "",
                "system:data-overdue",
                None,
                None,
                NotificationEvent.DATA_OVERDUE,
                None,
                None,
                False,
                None,
                None,
                True,
                None,
                True,
                ProducerRedactionHits(0, 0),
            ),
        ),
    )
    with pytest.raises(TelescreenError) as raised:
        await inbox.receive(changed, Producer.INVENTORY)
    assert raised.value.code is ErrorCode.CONFLICT
    await harness.close()


async def test_existing_retention_receipt_deduplicates_inventory_command(
    inbox_world: tuple[SqliteHarness, FakeClock, NotificationInbox],
) -> None:
    """A copied obligation receipt prevents re-sending its overdue notification."""
    harness: SqliteHarness
    clock: FakeClock
    inbox: NotificationInbox
    harness, clock, inbox = inbox_world
    command: NotificationCommand = _overdue_command(clock.now() - timedelta(seconds=601))
    async with harness.sessions.begin() as session:
        session.add(
            RetentionNotificationReceiptRow(
                id=command.event_id,
                payload_sha256="a" * 64,
                applied_at=clock.now(),
            )
        )

    result: NotificationCommandReceipt = await inbox.receive(command, Producer.INVENTORY)

    assert result is NotificationCommandReceipt.DUPLICATE
    async with harness.sessions() as session:
        inbox_count: int = (
            await session.scalar(select(func.count()).select_from(NotificationInboxRow)) or 0
        )
        row_count: int = (
            await session.scalar(select(func.count()).select_from(NotificationRow)) or 0
        )
    assert inbox_count == row_count == 0
    await harness.close()


async def test_inventory_producer_rejects_unsupported_operations_without_writes(
    inbox_world: tuple[SqliteHarness, FakeClock, NotificationInbox],
) -> None:
    """Reject directory, event-less and non-overdue inventory operations before writes."""
    harness: SqliteHarness
    clock: FakeClock
    inbox: NotificationInbox
    harness, clock, inbox = inbox_world
    directory: DirectoryOperation = DirectoryOperation(
        DirectoryEntity(
            DirectoryEntityKind.MEMBER,
            "member-1",
            1,
            True,
            MemberEntity("member-1", "alice", "Alice", True, "admin", "alice@example.test", None),
        )
    )
    operations: tuple[NotificationOperation, ...] = (
        directory,
        SendOperation(
            NotificationChannel.EMAIL,
            ("member:missing",),
            "",
            "",
            "system:test",
            None,
            None,
            None,
            None,
            None,
            False,
            None,
            None,
            True,
            None,
            True,
            ProducerRedactionHits(0, 0),
        ),
        SendOperation(
            NotificationChannel.EMAIL,
            ("member:missing",),
            "",
            "",
            "system:test",
            None,
            None,
            NotificationEvent.ISSUE_ASSIGNED,
            None,
            None,
            False,
            None,
            None,
            True,
            None,
            True,
            ProducerRedactionHits(0, 0),
        ),
        MemberSendOperation(
            "member-1",
            NotificationCategory.ALERTS,
            NotificationEvent.ISSUE_ASSIGNED,
            NotificationContext(),
            None,
            ProducerRedactionHits(0, 0),
        ),
    )
    for index, operation in enumerate(operations):
        command: NotificationCommand = NotificationCommand(
            f"event-forbidden-{index}", clock.now() - timedelta(seconds=601), (operation,)
        )
        with pytest.raises(TelescreenError) as raised:
            await inbox.receive(command, Producer.INVENTORY)
        assert raised.value.code is ErrorCode.FORBIDDEN
        async with harness.sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(NotificationInboxRow)) or 0
            ) == 0
            assert (
                await session.scalar(select(func.count()).select_from(NotificationRow)) or 0
            ) == 0
            assert (
                await session.scalar(
                    select(func.count()).select_from(NotificationDirectoryEntryRow)
                )
                or 0
            ) == 0

    telescreen_command: NotificationCommand = NotificationCommand(
        "event-telescreen-directory",
        clock.now(),
        (directory,),
    )
    result: NotificationCommandReceipt = await inbox.receive(
        telescreen_command, Producer.TELESCREEN
    )
    assert result is NotificationCommandReceipt.APPLIED
    async with harness.sessions() as session:
        applied: DirectoryEntity | None = await inbox.replica.get(
            session, DirectoryEntityKind.MEMBER, "member-1"
        )
    assert applied == directory.entity
    await harness.close()


async def test_team_send_uses_deterministic_recipient_row_ids(
    inbox_world: tuple[SqliteHarness, FakeClock, NotificationInbox],
) -> None:
    """Fan out one team send into stable row ids by recipient position."""
    harness: SqliteHarness
    clock: FakeClock
    inbox: NotificationInbox
    harness, clock, inbox = inbox_world
    operation: SendOperation = SendOperation(
        NotificationChannel.EMAIL,
        ("team:ops", "member:alice"),
        "Team notice",
        "Safe body",
        "system:team-test",
        None,
        None,
        None,
        None,
        None,
        False,
        None,
        None,
        True,
        None,
        False,
        ProducerRedactionHits(0, 0),
    )
    command: NotificationCommand = NotificationCommand("event-team-send", clock.now(), (operation,))
    result: NotificationCommandReceipt = await inbox.receive(command, Producer.TELESCREEN)
    assert result is NotificationCommandReceipt.APPLIED
    async with harness.sessions() as session:
        rows: tuple[NotificationRow, ...] = tuple(
            await session.scalars(select(NotificationRow).order_by(NotificationRow.recipient_ref))
        )
    assert len(rows) == 2
    team_row: NotificationRow = next(row for row in rows if row.recipient_ref == "team:ops")
    member_row: NotificationRow = next(row for row in rows if row.recipient_ref == "member:alice")
    assert team_row.id == notification_row_id("event-team-send:0", 0)
    assert member_row.id == notification_row_id("event-team-send:0", 1)
    await harness.close()
