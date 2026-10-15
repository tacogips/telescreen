"""Outbox producer redaction, recipient checks and deterministic receipts."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DlpVerdict, NotificationChannel
from telescreen.db.models.notification_handoff import NotificationOutboxRow
from telescreen.integrations.notification_outbox import NotificationOutbox
from telescreen.integrations.notification_publish import (
    NotificationOutboxPublisher,
    OutboxNotificationSender,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.directory import ResolvedRecipients
from telescreen.ports.notification_commands import (
    NotificationCommand,
    NotificationOperation,
    SendOperation,
    decode_notification_command,
    notification_row_id,
    operation_seed,
)
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationReceipt,
    NotificationRequest,
)
from tests.notifications.v13.fakes import RecordingQueue, StaticResolver
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import aws_access_key_id

_NOW: datetime = datetime(2026, 10, 16, tzinfo=UTC)


@pytest.mark.asyncio
async def test_sender_redacts_context_and_returns_deterministic_pending_receipt() -> None:
    """Persist masked text plus hit counts only and return the event-derived row id."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(_NOW)
    queue: RecordingQueue = RecordingQueue()
    outbox: NotificationOutbox = NotificationOutbox()
    publisher: NotificationOutboxPublisher = NotificationOutboxPublisher(outbox, queue, clock)
    sender: OutboxNotificationSender = OutboxNotificationSender(
        StaticResolver(
            (
                (
                    "member:alice",
                    ResolvedRecipients(("alice@example.invalid",), (), (), ("member-1",)),
                ),
            )
        ),
        Redactor.default(),
        publisher,
    )
    secret: str = aws_access_key_id().value
    request: NotificationRequest = NotificationRequest(
        NotificationChannel.EMAIL,
        ("member:alice",),
        f"credential {secret}",
        "masked body",
        "system:test",
        context=NotificationContext(issue_title=secret),
        inline=True,
    )
    try:
        async with harness.sessions.begin() as session:
            receipt: NotificationReceipt = await sender.send(session, request)
        async with harness.sessions() as session:
            rows: tuple[NotificationOutboxRow, ...] = tuple(
                (await session.scalars(select(NotificationOutboxRow))).all()
            )
            row: NotificationOutboxRow = rows[0]
            command: NotificationCommand = decode_notification_command(row.payload_json)
            operation: NotificationOperation = command.operations[0]
            assert isinstance(operation, SendOperation)
            assert secret not in row.payload_json
            assert operation.context is not None
            assert operation.context.issue_title is not None
            assert secret not in operation.context.issue_title
            assert operation.redaction.subject == 1
            assert operation.redaction.context[0].field == "issue_title"
            assert operation.redaction.context[0].count == 1
            expected_id: str = notification_row_id(operation_seed(command.event_id, 0), 0)
            assert receipt.notification_ids == (expected_id,)
            assert receipt.verdict is DlpVerdict.PASS
            assert receipt.queued is True and receipt.pending is True
        assert queue.specs[0].ref_id == command.event_id
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_unknown_recipient_fails_before_outbox_write() -> None:
    """Reject unknown refs when unreachable outcomes were not requested."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(_NOW)
    sender: OutboxNotificationSender = OutboxNotificationSender(
        StaticResolver(()),
        Redactor.default(),
        NotificationOutboxPublisher(NotificationOutbox(), RecordingQueue(), clock),
    )
    try:
        with pytest.raises(TelescreenError) as failure:
            async with harness.sessions.begin() as session:
                await sender.send(
                    session,
                    NotificationRequest(
                        NotificationChannel.EMAIL,
                        ("member:missing",),
                        "safe",
                        "safe",
                        "system:test",
                    ),
                )
        assert failure.value.code.value == "recipient_unknown"
        async with harness.sessions() as session:
            assert (await session.scalars(select(NotificationOutboxRow))).all() == []
    finally:
        await harness.close()
