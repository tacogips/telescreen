"""Single-process and split-path DLP parity for producer redaction counts."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import DlpPolicy, NotificationChannel, NotificationEvent
from telescreen.db.models import NotificationDirectoryEntryRow, NotificationRow
from telescreen.db.ownership import Store
from telescreen.masking.redact import RedactionResult, Redactor
from telescreen.notification_service.inbox import NotificationInbox, Producer
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.ports.directory import ResolvedRecipients
from telescreen.ports.notification_commands import (
    NotificationCommand,
    ProducerRedactionHits,
    SendOperation,
    decode_notification_command,
    encode_notification_command,
)
from telescreen.ports.notification_types import ContextFieldHits, NotificationContext
from telescreen.ports.notifications import NotificationRequest
from telescreen.replica.store import DirectoryReplica
from tests.notifications.v12.fakes import make_gate
from tests.notifications.v13.fakes import RecordingAudit, RecordingQueue, StaticResolver
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import github_token


@dataclass(frozen=True)
class RedactedInput:
    """Masked operation fields paired with count-only producer hit metadata."""

    subject: str
    body: str
    context: NotificationContext | None
    hits: ProducerRedactionHits


def _producer_redact(subject: str, body: str, context: NotificationContext | None) -> RedactedInput:
    """Apply the producer-side rule redactor and retain only hit counts."""
    redactor: Redactor = Redactor.default()
    safe_subject: RedactionResult = redactor.redact(subject)
    safe_body: RedactionResult = redactor.redact(body)
    safe_context: NotificationContext | None = context
    context_hits: tuple[ContextFieldHits, ...] = ()
    if context is not None and context.issue_title is not None:
        safe_title: RedactionResult = redactor.redact(context.issue_title)
        safe_context = NotificationContext(issue_title=safe_title.text)
        if safe_title.hit_count:
            context_hits = (ContextFieldHits("issue_title", safe_title.hit_count),)
    return RedactedInput(
        safe_subject.text,
        safe_body.text,
        safe_context,
        ProducerRedactionHits(safe_subject.hit_count, safe_body.hit_count, context_hits),
    )


@pytest.mark.parametrize("policy", (DlpPolicy.MASK, DlpPolicy.BLOCK))
@pytest.mark.parametrize("path", ("v1", "inline", "context"))
async def test_single_and_split_dlp_results_match(policy: DlpPolicy, path: str) -> None:
    """Fold producer-side hits into the same verdict and hit count as local sends."""
    raw: str = github_token().value
    event: NotificationEvent | None = None
    inline: bool = False
    context: NotificationContext | None = None
    subject: str = "Notice"
    body: str = f"Token: {raw}"
    if path == "inline":
        event = NotificationEvent.ISSUE_ASSIGNED
        inline = True
    elif path == "context":
        event = NotificationEvent.ISSUE_ASSIGNED
        body = ""
        context = NotificationContext(issue_title=f"Token: {raw}")
    clock: FakeClock = FakeClock(datetime(2026, 10, 16, tzinfo=UTC))
    resolved: ResolvedRecipients = ResolvedRecipients(("member@example.test",), (), (), ())
    resolver: StaticResolver = StaticResolver((("email:member@example.test", resolved),))
    single_harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    single_audit: RecordingAudit = RecordingAudit()
    single_service: NotificationService = NotificationService(
        make_gate(policy),
        resolver,
        RecordingQueue(),
        single_audit,
        clock,
        NotificationRepository(),
    )
    single_request: NotificationRequest = NotificationRequest(
        NotificationChannel.EMAIL,
        ("email:member@example.test",),
        subject,
        body,
        "system:parity-test",
        event=event,
        context=context,
        inline=inline,
    )
    async with single_harness.sessions.begin() as session:
        await single_service.send(session, single_request)
    async with single_harness.sessions() as session:
        single_row: NotificationRow = (await session.scalars(select(NotificationRow))).one()

    split_harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    split_audit: RecordingAudit = RecordingAudit()
    split_service: NotificationService = NotificationService(
        make_gate(policy),
        resolver,
        RecordingQueue(),
        split_audit,
        clock,
        NotificationRepository(),
    )
    inbox: NotificationInbox = NotificationInbox(
        split_harness.sessions,
        split_service,
        resolver,
        DirectoryReplica(NotificationDirectoryEntryRow),
        clock,
        600,
    )
    redacted: RedactedInput = _producer_redact(subject, body, context)
    operation: SendOperation = SendOperation(
        NotificationChannel.EMAIL,
        ("email:member@example.test",),
        redacted.subject,
        redacted.body,
        "system:parity-test",
        None,
        None,
        event,
        None,
        redacted.context,
        inline,
        None,
        None,
        False,
        None,
        False,
        redacted.hits,
    )
    command: NotificationCommand = NotificationCommand("parity-event-1", clock.now(), (operation,))
    wire: str = encode_notification_command(command)
    await inbox.receive(decode_notification_command(wire), Producer.TELESCREEN)
    async with split_harness.sessions() as session:
        split_row: NotificationRow = (await session.scalars(select(NotificationRow))).one()

    assert split_row.dlp_verdict == single_row.dlp_verdict
    assert split_row.dlp_hit_count == single_row.dlp_hit_count > 0
    assert len(single_audit.entries) == len(split_audit.entries) == 1
    assert split_audit.entries[0].action == single_audit.entries[0].action
    assert raw not in single_row.subject + single_row.body
    assert raw not in split_row.subject + split_row.body
    if policy is DlpPolicy.BLOCK:
        assert single_row.subject == single_row.body == "[BLOCKED BY DLP]"
        assert split_row.subject == split_row.body == "[BLOCKED BY DLP]"
    await single_harness.close()
    await split_harness.close()
