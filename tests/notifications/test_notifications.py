"""Tests for outbound DLP, durable notification enqueue, and delivery."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_null
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import (
    DetectorKind,
    DlpPolicy,
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
    NotificationChannel,
    NotificationStatus,
    RuleClass,
)
from telescreen.db.models.notifications import NotificationRow
from telescreen.detection import RuleDetector
from telescreen.detection.types import DetectionResult, Detector, RawFinding, ScanItem, Span
from telescreen.masking.redact import Redactor
from telescreen.notifications.delivery import NotificationDeliveryHandler
from telescreen.notifications.dlp import CensoredMessage, DlpGate, OutboundMessage, _Seal
from telescreen.notifications.email import EmailNotifier
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.notifications.slack import SlackNotifier
from telescreen.ports.audit import AuditEntry
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind, TaskSpec
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import assert_no_leaks, github_token


@dataclass
class RecordingAudit:
    """Collect audit entries without opening a separate transaction."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one safe audit event."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class RecordingQueue:
    """Collect durable task specifications for assertions."""

    specs: list[TaskSpec] = field(default_factory=list)

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Record a task spec."""
        del session
        self.specs.append(spec)
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Implement the unused queue operation."""
        del session, ref_id
        return 0


@dataclass
class FakeResolver:
    """Resolve a known test reference into email and Slack targets."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Return fixed targets or reject the configured unknown reference."""
        del session
        if ref == "unknown:target":
            raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient reference is unknown")
        return ResolvedRecipients(("oncall@example.invalid",), ("C123",), ("U123",), ())

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Implement the unused membership operation."""
        del session, member_id, ref
        return False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Implement the unused assignee operation."""
        del session, ref
        return AssigneeIds(None, None)


@dataclass(frozen=True)
class SubjectFindingDetector:
    """Return one synthetic finding in the subject and leave the body untouched."""

    kind: DetectorKind = DetectorKind.RULE

    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]:
        """Report a finding over the subject only."""
        results: list[DetectionResult] = []
        for item in items:
            findings: tuple[RawFinding, ...] = ()
            if item.item_id == "subject":
                findings = (
                    RawFinding(
                        "synthetic_secret",
                        RuleClass.CREDENTIAL,
                        Span(0, len(item.text)),
                        1.0,
                        DetectorKind.RULE,
                    ),
                )
            results.append(DetectionResult(item.item_id, findings))
        return results


@dataclass
class RecordingSmtp:
    """Capture a message passed to the SMTP transport."""

    messages: list[EmailMessage] = field(default_factory=list)

    def send(self, message: EmailMessage) -> None:
        """Record the complete email message."""
        self.messages.append(message)


def make_service(
    queue: RecordingQueue,
    audit: RecordingAudit,
    resolver: FakeResolver,
    clock: FakeClock,
    policy: DlpPolicy = DlpPolicy.MASK,
    detector: Detector | None = None,
) -> NotificationService:
    """Create the notification service with the real rule-based redactor."""
    redactor: Redactor = Redactor.default()
    gate: DlpGate = DlpGate(detector or RuleDetector(), redactor, policy)
    return NotificationService(gate, resolver, queue, audit, clock, NotificationRepository())


def request(
    subject: str,
    body: str,
    recipients: tuple[str, ...] = ("team:responders",),
    channel: NotificationChannel = NotificationChannel.SLACK,
) -> NotificationRequest:
    """Create a notification request with non-sensitive routing metadata."""
    return NotificationRequest(channel, recipients, subject, body, "test-actor")


async def test_mask_gate_persists_safe_row_and_delivery_task() -> None:
    """Mask sample data before storage and enqueue one idempotent task per recipient."""
    harness: SqliteHarness = await make_sqlite_harness()
    queue: RecordingQueue = RecordingQueue()
    audit: RecordingAudit = RecordingAudit()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    service: NotificationService = make_service(queue, audit, FakeResolver(), clock)
    token: str = github_token().value
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send(
            session,
            request("Finding", f"Token: {token}", ("team:responders", "member:alice")),
        )
        assert receipt.verdict is DlpVerdict.MASKED
        assert receipt.queued
        assert len(receipt.notification_ids) == 2
    async with harness.sessions() as session:
        rows: list[NotificationRow] = list((await session.scalars(select(NotificationRow))).all())
    assert len(rows) == 2
    row: NotificationRow = rows[0]
    assert row.status == NotificationStatus.QUEUED.value
    assert row.dlp_verdict == DlpVerdict.MASKED.value
    assert_no_leaks(row.subject + row.body, "notification row")
    assert len(queue.specs) == 2
    assert queue.specs[0].kind is EngineTaskKind.NOTIFICATION_DELIVERY
    assert {spec.idempotency_key for spec in queue.specs} == {
        f"notify:{saved.id}" for saved in rows
    }
    assert [entry.action for entry in audit.entries] == [
        "notification.queued",
        "notification.queued",
    ]
    await harness.close()


async def test_clean_message_passes_and_queues_unchanged_content() -> None:
    """Pass clean text unchanged and enqueue one delivery task."""
    harness: SqliteHarness = await make_sqlite_harness()
    queue: RecordingQueue = RecordingQueue()
    audit: RecordingAudit = RecordingAudit()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    service: NotificationService = make_service(queue, audit, FakeResolver(), clock)
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send(
            session, request("Routine update", "The report is ready.")
        )
    async with harness.sessions() as session:
        row: NotificationRow = (await session.scalars(select(NotificationRow))).one()
    assert receipt.verdict is DlpVerdict.PASS
    assert receipt.queued
    assert row.subject == "Routine update"
    assert row.body == "The report is ready."
    assert row.status == NotificationStatus.QUEUED.value
    assert len(queue.specs) == 1
    assert queue.specs[0].idempotency_key == f"notify:{row.id}"
    assert [entry.action for entry in audit.entries] == ["notification.queued"]
    await harness.close()


async def test_block_policy_stores_placeholder_and_does_not_enqueue() -> None:
    """Persist only the block placeholder and audit without creating delivery work."""
    harness: SqliteHarness = await make_sqlite_harness()
    queue: RecordingQueue = RecordingQueue()
    audit: RecordingAudit = RecordingAudit()
    service: NotificationService = make_service(
        queue,
        audit,
        FakeResolver(),
        FakeClock(datetime(2026, 10, 9, tzinfo=UTC)),
        DlpPolicy.BLOCK,
    )
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send(
            session, request("Notice", github_token().value)
        )
        assert receipt.verdict is DlpVerdict.BLOCKED
        assert not receipt.queued
    async with harness.sessions() as session:
        row: NotificationRow = (await session.scalars(select(NotificationRow))).one()
    assert row.status == NotificationStatus.BLOCKED.value
    assert row.subject == row.body == "[BLOCKED BY DLP]"
    assert queue.specs == []
    assert [entry.action for entry in audit.entries] == ["notification.blocked"]
    await harness.close()


async def test_mask_policy_blocks_if_rule_redactor_finds_residual_value() -> None:
    """Fail closed when a configured detector leaves a rule-detectable body value."""
    gate: DlpGate = DlpGate(SubjectFindingDetector(), Redactor.default(), DlpPolicy.MASK)
    message: CensoredMessage = await gate.inspect(OutboundMessage("notice", github_token().value))
    assert message.verdict is DlpVerdict.BLOCKED
    assert message.subject == message.body == "[BLOCKED BY DLP]"
    assert message.hit_count == 1


def test_censored_message_cannot_be_constructed_without_package_seal() -> None:
    """Reject direct construction by callers that do not possess the package seal."""
    with pytest.raises(TypeError):
        CensoredMessage("subject", "body", DlpVerdict.PASS, 0, _Seal())


async def test_unknown_recipient_fails_before_any_row_is_inserted() -> None:
    """Reject an unknown recipient before writing notification or task state."""
    harness: SqliteHarness = await make_sqlite_harness()
    queue: RecordingQueue = RecordingQueue()
    audit: RecordingAudit = RecordingAudit()
    service: NotificationService = make_service(
        queue,
        audit,
        FakeResolver(),
        FakeClock(datetime(2026, 10, 9, tzinfo=UTC)),
    )
    with pytest.raises(TelescreenError) as raised:
        async with harness.sessions.begin() as session:
            await service.send(session, request("Notice", "safe", ("unknown:target",)))
    assert raised.value.code is ErrorCode.RECIPIENT_UNKNOWN
    async with harness.sessions() as session:
        rows: list[NotificationRow] = list((await session.scalars(select(NotificationRow))).all())
    assert rows == []
    assert queue.specs == []
    assert audit.entries == []
    await harness.close()


async def test_delivery_sends_once_and_skips_duplicate_task() -> None:
    """Send through Slack once and make a replay of the task idempotent."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    async with harness.sessions.begin() as session:
        session.add(_notification("notification-1", clock.now()))
    posted: list[bytes] = []

    def handler(request_value: httpx.Request) -> httpx.Response:
        posted.append(request_value.content)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        slack: SlackNotifier = SlackNotifier(client, SecretValue("test-slack-token"))
        smtp: RecordingSmtp = RecordingSmtp()
        audit: RecordingAudit = RecordingAudit()
        delivery: NotificationDeliveryHandler = NotificationDeliveryHandler(
            harness.sessions,
            FakeResolver(),
            slack,
            EmailNotifier(smtp, "noreply@example.invalid"),
            audit,
            clock,
            NotificationRepository(),
        )
        task: ClaimedTask = _claimed("notification-1", 1, 5)
        first: TaskResult = await delivery.handle(task)
        second: TaskResult = await delivery.handle(task)
    assert first.kind is TaskResultKind.DONE
    assert second.kind is TaskResultKind.DONE
    assert len(posted) == 2
    assert any(b"C123" in body for body in posted)
    assert any(b"U123" in body for body in posted)
    assert all(b"Masked subject\\nMasked body" in body for body in posted)
    async with harness.sessions() as session:
        saved: NotificationRow | None = await session.get(NotificationRow, "notification-1")
    assert saved is not None
    assert saved.status == NotificationStatus.SENT.value
    assert saved.delivery_attempts == 1
    assert saved.sent_at is not None
    assert ensure_utc(saved.sent_at) == clock.now()
    assert [entry.action for entry in audit.entries] == ["notification.sent"]
    await harness.close()


async def test_delivery_rate_limit_uses_retry_after() -> None:
    """Return a retry disposition scheduled at Slack's Retry-After value."""
    harness: SqliteHarness = await _queued_harness("notification-rate-limit")
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))

    def handler(request_value: httpx.Request) -> httpx.Response:
        del request_value
        return httpx.Response(429, headers={"Retry-After": "17"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        delivery: NotificationDeliveryHandler = _delivery(harness, clock, client, RecordingAudit())
        result: TaskResult = await delivery.handle(_claimed("notification-rate-limit", 1, 5))
    assert result.kind is TaskResultKind.RETRY
    assert result.error_code is ErrorCode.RATE_LIMITED
    assert result.available_at == clock.now() + timedelta(seconds=17)
    async with harness.sessions() as session:
        row: NotificationRow | None = await session.get(NotificationRow, "notification-rate-limit")
    assert row is not None
    assert row.status == NotificationStatus.QUEUED.value
    assert row.delivery_attempts == 1
    await harness.close()


async def test_delivery_backoff_and_terminal_failure() -> None:
    """Use exponential retries and mark the final failed attempt in the row."""
    harness: SqliteHarness = await _queued_harness("notification-failure")
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: RecordingAudit = RecordingAudit()

    def handler(request_value: httpx.Request) -> httpx.Response:
        del request_value
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        delivery: NotificationDeliveryHandler = _delivery(harness, clock, client, audit)
        retry: TaskResult = await delivery.handle(_claimed("notification-failure", 2, 5))
        dead: TaskResult = await delivery.handle(_claimed("notification-failure", 5, 5))
    assert retry.kind is TaskResultKind.RETRY
    assert retry.available_at == clock.now() + timedelta(seconds=60)
    assert dead.kind is TaskResultKind.DEAD
    assert dead.error_code is ErrorCode.DELIVERY_FAILED
    async with harness.sessions() as session:
        row: NotificationRow | None = await session.get(NotificationRow, "notification-failure")
    assert row is not None
    assert row.status == NotificationStatus.FAILED.value
    assert row.delivery_attempts == 5
    assert [entry.action for entry in audit.entries] == ["notification.failed"]
    await harness.close()


async def test_slack_adapter_posts_sealed_message_with_bearer_auth() -> None:
    """Send the sealed subject/body through the expected Slack endpoint."""
    observed: list[httpx.Request] = []

    def handler(request_value: httpx.Request) -> httpx.Response:
        observed.append(request_value)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier: SlackNotifier = SlackNotifier(client, SecretValue("example-token"))
        censored: CensoredMessage = await DlpGate(
            RuleDetector(), Redactor.default(), DlpPolicy.MASK
        ).inspect(OutboundMessage("Alert", f"Token {github_token().value}"))
        await notifier.send("C123", censored)
    assert len(observed) == 1
    assert observed[0].url.path.endswith("/chat.postMessage")
    assert observed[0].headers["Authorization"] == "Bearer example-token"
    assert b"C123" in observed[0].content
    assert_no_leaks(observed[0].content.decode(), "Slack request")


async def test_email_notifier_receives_only_censored_content() -> None:
    """Build an email using sealed content with no raw sample leak."""
    smtp: RecordingSmtp = RecordingSmtp()
    notifier: EmailNotifier = EmailNotifier(smtp, "noreply@example.invalid")
    message: CensoredMessage = await DlpGate(
        RuleDetector(), Redactor.default(), DlpPolicy.MASK
    ).inspect(OutboundMessage("Alert", f"Token {github_token().value}"))
    await notifier.send("oncall@example.invalid", message)
    assert len(smtp.messages) == 1
    assert smtp.messages[0]["To"] == "oncall@example.invalid"
    assert_no_leaks(smtp.messages[0].as_string(), "email payload")


def _claimed(notification_id: str, attempt: int, max_attempts: int) -> ClaimedTask:
    """Create a delivery task for a selected attempt."""
    return ClaimedTask(
        f"task-{attempt}",
        EngineTaskKind.NOTIFICATION_DELIVERY,
        notification_id,
        json_null(),
        attempt,
        max_attempts,
        f"notify:{notification_id}",
    )


def _notification(notification_id: str, created_at: datetime) -> NotificationRow:
    """Create a queued Slack row with already-censored content."""
    return NotificationRow(
        id=notification_id,
        channel=NotificationChannel.SLACK.value,
        recipient_ref="team:responders",
        subject="Masked subject",
        body="Masked body",
        dlp_verdict=DlpVerdict.MASKED.value,
        dlp_hit_count=1,
        status=NotificationStatus.QUEUED.value,
        delivery_attempts=0,
        run_id=None,
        issue_id=None,
        created_at=created_at,
        sent_at=None,
    )


async def _queued_harness(notification_id: str) -> SqliteHarness:
    """Create a harness with one queued Slack notification."""
    harness: SqliteHarness = await make_sqlite_harness()
    async with harness.sessions.begin() as session:
        session.add(_notification(notification_id, datetime(2026, 10, 9, tzinfo=UTC)))
    return harness


def _delivery(
    harness: SqliteHarness,
    clock: FakeClock,
    client: httpx.AsyncClient,
    audit: RecordingAudit,
) -> NotificationDeliveryHandler:
    """Create a delivery handler with fake recipient resolution and SMTP."""
    return NotificationDeliveryHandler(
        harness.sessions,
        FakeResolver(),
        SlackNotifier(client, SecretValue("test-slack-token")),
        EmailNotifier(RecordingSmtp(), "noreply@example.invalid"),
        audit,
        clock,
        NotificationRepository(),
    )
