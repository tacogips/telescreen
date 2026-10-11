"""Template rendering in the sender, per-target rows, delivery fix and Slack result."""

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json, json_null
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import (
    DlpPolicy,
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
    NotificationChannel,
    NotificationEvent,
    NotificationStatus,
)
from telescreen.db.models.notifications import NotificationRow
from telescreen.notifications.delivery import NotificationDeliveryHandler
from telescreen.notifications.dlp import CensoredMessage, OutboundMessage
from telescreen.notifications.email import EmailNotifier
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.notifications.slack import SlackNotifier, SlackPostResult
from telescreen.notifications.templates.model import TemplateTexts
from telescreen.notifications.templates.service import TemplateService
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationReceipt,
    NotificationRequest,
)
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind
from tests.notifications.v12.fakes import (
    MapResolver,
    RecordingAudit,
    RecordingQueue,
    RecordingSmtp,
    make_gate,
    make_sender,
    make_templates,
)
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import assert_no_leaks, github_token

NOW: datetime = datetime(2026, 10, 11, tzinfo=UTC)
CONTEXT: NotificationContext = NotificationContext(
    issue_short_id="TELE-4",
    issue_rule_class="token",
    issue_severity="high",
    issue_exposure="public",
)


async def all_rows(harness: SqliteHarness) -> list[NotificationRow]:
    """Load every notification row."""
    async with harness.sessions() as session:
        return list((await session.scalars(select(NotificationRow))).all())


async def test_event_request_renders_builtin_and_records_template() -> None:
    """An event request stores the rendered built-in text and template metadata."""
    harness: SqliteHarness = await make_sqlite_harness()
    queue: RecordingQueue = RecordingQueue()
    audit: RecordingAudit = RecordingAudit()
    service: NotificationService = make_sender(queue, audit, FakeClock(NOW))
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send(
            session,
            NotificationRequest(
                NotificationChannel.EMAIL,
                ("member:alice",),
                "",
                "",
                "system:test",
                event=NotificationEvent.EXCEPTION_REQUESTED,
                context=CONTEXT,
            ),
        )
    row: NotificationRow = (await all_rows(harness))[0]
    assert receipt.queued and receipt.verdict is DlpVerdict.PASS and receipt.skipped == ()
    assert row.subject == "Exception requested for TELE-4"
    assert row.body == "TELE-4 token high public"
    assert (row.event_kind, row.template_name, row.template_version) == (
        "exception_requested",
        "system.exception_requested",
        0,
    )
    assert row.status == NotificationStatus.QUEUED.value and row.error_code is None
    assert len(queue.specs) == 1
    assert dump_json(audit.entries[0].details) == (
        '{"channel":"email","event_kind":"exception_requested","hit_count":0,'
        '"template_name":"system.exception_requested","template_version":0,"verdict":"pass"}'
    )
    await harness.close()


async def test_active_db_template_and_fanout_fields_are_recorded() -> None:
    """A referenced active DB template renders and set name and index are stored."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: RecordingAudit = RecordingAudit()
    templates: TemplateService = make_templates(audit, FakeClock(NOW))
    service: NotificationService = make_sender(RecordingQueue(), audit, FakeClock(NOW))
    async with harness.sessions.begin() as session:
        await templates.create_version(
            session,
            "team.alert",
            NotificationEvent.WORKFLOW_NOTIFY,
            TemplateTexts("Alert {{ issue.short_id }}", "Run {{ run.workflow }}"),
            "member:a",
        )
        await templates.activate(session, "team.alert", 1, "member:a")
        await service.send(
            session,
            NotificationRequest(
                NotificationChannel.SLACK,
                ("member:bob",),
                "",
                "",
                "system:workflow",
                event=NotificationEvent.WORKFLOW_NOTIFY,
                template_name="team.alert",
                context=NotificationContext(issue_short_id="TELE-4", run_workflow="triage"),
                set_name="owners",
                target_index=1,
                record_unreachable=True,
            ),
        )
    row: NotificationRow = (await all_rows(harness))[0]
    assert (row.subject, row.body) == ("Alert TELE-4", "Run triage")
    assert (row.template_name, row.template_version, row.set_name, row.target_index) == (
        "team.alert",
        1,
        "owners",
        1,
    )
    await harness.close()


@pytest.mark.parametrize(
    ("ref", "channel", "code"),
    (
        ("email:x@example.test", NotificationChannel.SLACK, ErrorCode.RECIPIENT_UNREACHABLE),
        ("member:bob", NotificationChannel.EMAIL, ErrorCode.RECIPIENT_UNREACHABLE),
        ("member:ghost", NotificationChannel.SLACK, ErrorCode.RECIPIENT_UNKNOWN),
    ),
)
async def test_record_unreachable_writes_skipped_rows(
    ref: str, channel: NotificationChannel, code: ErrorCode
) -> None:
    """Unreachable or unknown refs become skipped rows with no delivery task."""
    harness: SqliteHarness = await make_sqlite_harness()
    queue: RecordingQueue = RecordingQueue()
    audit: RecordingAudit = RecordingAudit()
    service: NotificationService = make_sender(queue, audit, FakeClock(NOW))
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send(
            session,
            NotificationRequest(
                channel,
                (ref, "team:security"),
                "",
                "",
                "system:workflow",
                event=NotificationEvent.WORKFLOW_NOTIFY,
                context=CONTEXT,
                set_name="owners",
                target_index=0,
                record_unreachable=True,
            ),
        )
    rows: list[NotificationRow] = await all_rows(harness)
    skipped: NotificationRow = next(row for row in rows if row.recipient_ref == ref)
    queued: NotificationRow = next(row for row in rows if row.recipient_ref == "team:security")
    assert skipped.status == NotificationStatus.SKIPPED.value
    assert skipped.error_code == code.value
    assert skipped.subject == queued.subject
    assert queued.status == NotificationStatus.QUEUED.value
    assert receipt.skipped == (skipped.id,)
    assert receipt.notification_ids == (queued.id,)
    assert receipt.queued
    assert [spec.ref_id for spec in queue.specs] == [queued.id]
    actions: list[str] = [entry.action for entry in audit.entries]
    assert actions == ["notification.skipped", "notification.queued"]
    assert f'"error_code":"{code.value}"' in dump_json(audit.entries[0].details)
    await harness.close()


async def test_all_skipped_request_is_not_queued() -> None:
    """A request whose only ref is unreachable queues nothing."""
    harness: SqliteHarness = await make_sqlite_harness()
    queue: RecordingQueue = RecordingQueue()
    service: NotificationService = make_sender(queue, RecordingAudit(), FakeClock(NOW))
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send(
            session,
            NotificationRequest(
                NotificationChannel.SLACK,
                ("email:x@example.test",),
                "",
                "",
                "system:workflow",
                event=NotificationEvent.WORKFLOW_NOTIFY,
                record_unreachable=True,
            ),
        )
    assert not receipt.queued and receipt.notification_ids == () and len(receipt.skipped) == 1
    assert queue.specs == []
    await harness.close()


@pytest.mark.parametrize(
    ("policy", "verdict", "status"),
    (
        (DlpPolicy.MASK, DlpVerdict.MASKED, NotificationStatus.QUEUED),
        (DlpPolicy.BLOCK, DlpVerdict.BLOCKED, NotificationStatus.BLOCKED),
    ),
)
async def test_inline_event_text_is_redacted_and_gated(
    policy: DlpPolicy, verdict: DlpVerdict, status: NotificationStatus
) -> None:
    """Inline text with a sample is masked (or blocked) and never stored raw."""
    harness: SqliteHarness = await make_sqlite_harness()
    service: NotificationService = make_sender(
        RecordingQueue(), RecordingAudit(), FakeClock(NOW), policy
    )
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send(
            session,
            NotificationRequest(
                NotificationChannel.EMAIL,
                ("member:alice",),
                "Inline notice",
                f"Token: {github_token().value}",
                "system:workflow",
                event=NotificationEvent.WORKFLOW_NOTIFY,
                inline=True,
            ),
        )
    row: NotificationRow = (await all_rows(harness))[0]
    assert receipt.verdict is verdict
    assert row.status == status.value
    assert row.dlp_hit_count >= 1
    assert (row.event_kind, row.template_name, row.template_version) == (
        "workflow_notify",
        None,
        None,
    )
    assert_no_leaks(row.subject + row.body, "inline notification row")
    await harness.close()


async def test_event_less_request_keeps_v1_unknown_recipient_failure() -> None:
    """Without an event and record_unreachable, an unknown ref still raises."""
    harness: SqliteHarness = await make_sqlite_harness()
    service: NotificationService = make_sender(RecordingQueue(), RecordingAudit(), FakeClock(NOW))
    with pytest.raises(TelescreenError) as raised:
        async with harness.sessions.begin() as session:
            await service.send(
                session,
                NotificationRequest(
                    NotificationChannel.SLACK, ("member:ghost",), "s", "b", "system:test"
                ),
            )
    assert raised.value.code is ErrorCode.RECIPIENT_UNKNOWN
    assert await all_rows(harness) == []
    await harness.close()


async def test_inspect_redacted_passes_clean_text_unchanged() -> None:
    """Clean rendered text keeps the pass verdict and zero hits."""
    message: CensoredMessage = await make_gate().inspect_redacted(
        OutboundMessage("Subject", "Body")
    )
    assert (message.subject, message.body, message.verdict, message.hit_count) == (
        "Subject",
        "Body",
        DlpVerdict.PASS,
        0,
    )


def queued_row(row_id: str, channel: NotificationChannel, ref: str) -> NotificationRow:
    """Build a queued notification row with masked text."""
    return NotificationRow(
        id=row_id,
        channel=channel.value,
        recipient_ref=ref,
        subject="Masked subject",
        body="Masked body",
        dlp_verdict=DlpVerdict.PASS.value,
        dlp_hit_count=0,
        status=NotificationStatus.QUEUED.value,
        delivery_attempts=0,
        run_id=None,
        issue_id=None,
        created_at=NOW,
        sent_at=None,
    )


def claimed(row_id: str) -> ClaimedTask:
    """Build a first-attempt delivery task."""
    return ClaimedTask(
        "task-1",
        EngineTaskKind.NOTIFICATION_DELIVERY,
        row_id,
        json_null(),
        1,
        5,
        f"notify:{row_id}",
    )


@pytest.mark.parametrize(
    ("ref", "code"),
    (
        ("member:bob", ErrorCode.RECIPIENT_UNREACHABLE),
        ("member:ghost", ErrorCode.RECIPIENT_UNKNOWN),
    ),
)
async def test_delivery_without_address_fails_and_sends_nothing(ref: str, code: ErrorCode) -> None:
    """A row whose recipient has no address on its channel fails instead of sent."""
    harness: SqliteHarness = await make_sqlite_harness()
    async with harness.sessions.begin() as session:
        session.add(queued_row("n-1", NotificationChannel.EMAIL, ref))
    smtp: RecordingSmtp = RecordingSmtp()
    audit: RecordingAudit = RecordingAudit()

    def unused(request_value: httpx.Request) -> httpx.Response:
        del request_value
        raise AssertionError("Slack must not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unused)) as client:
        handler: NotificationDeliveryHandler = NotificationDeliveryHandler(
            harness.sessions,
            MapResolver(),
            SlackNotifier(client, SecretValue("test-slack-token")),
            EmailNotifier(smtp, "noreply@example.test"),
            audit,
            FakeClock(NOW),
            NotificationRepository(),
        )
        result: TaskResult = await handler.handle(claimed("n-1"))
    row: NotificationRow = (await all_rows(harness))[0]
    assert result.kind is TaskResultKind.DEAD and result.error_code is code
    assert (row.status, row.error_code) == (NotificationStatus.FAILED.value, code.value)
    assert smtp.messages == []
    assert [entry.action for entry in audit.entries] == ["notification.failed"]
    await harness.close()


async def test_delivery_with_address_is_sent() -> None:
    """A reachable email row is delivered once and marked sent."""
    harness: SqliteHarness = await make_sqlite_harness()
    async with harness.sessions.begin() as session:
        session.add(queued_row("n-2", NotificationChannel.EMAIL, "member:alice"))
    smtp: RecordingSmtp = RecordingSmtp()

    def unused(request_value: httpx.Request) -> httpx.Response:
        del request_value
        raise AssertionError("Slack must not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unused)) as client:
        handler: NotificationDeliveryHandler = NotificationDeliveryHandler(
            harness.sessions,
            MapResolver(),
            SlackNotifier(client, SecretValue("test-slack-token")),
            EmailNotifier(smtp, "noreply@example.test"),
            RecordingAudit(),
            FakeClock(NOW),
            NotificationRepository(),
        )
        result: TaskResult = await handler.handle(claimed("n-2"))
    row: NotificationRow = (await all_rows(harness))[0]
    assert result.kind is TaskResultKind.DONE
    assert row.status == NotificationStatus.SENT.value and row.error_code is None
    assert [message["To"] for message in smtp.messages] == ["alice@example.test"]
    await harness.close()


async def test_slack_send_returns_post_result() -> None:
    """A successful post returns the reported channel and timestamp."""

    def handler(request_value: httpx.Request) -> httpx.Response:
        del request_value
        return httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "1.2"})

    message: CensoredMessage = await make_gate().inspect(OutboundMessage("s", "b"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result: SlackPostResult = await SlackNotifier(client, SecretValue("t")).send(
            "U12345678", message
        )
    assert result == SlackPostResult("C1", "1.2")
