"""Outbound DLP guard and inline notification channel tester tests."""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import DlpPolicy, ErrorCode, NotificationChannel
from telescreen.notifications.delivery import NotificationDeliveryHandler
from telescreen.notifications.email import EmailNotifier
from telescreen.notifications.guard import DlpOutboundGuard
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.notifications.slack import SlackNotifier
from telescreen.notifications.tester import NotificationServiceTester
from telescreen.ports.directory import OwnerDirectory, ResolvedRecipients
from telescreen.ports.notifications import GuardVerdict
from telescreen.ports.notifications import TestOutcome as NotificationTestOutcome
from tests.notifications.v12.fakes import make_gate
from tests.notifications.v13.fakes import RecordingAudit, RecordingQueue, StaticResolver, member_row
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import github_token


@dataclass(frozen=True)
class FakeMembers(OwnerDirectory):
    """Resolve only the member reference used by notification tests."""

    async def member_ref(self, session: AsyncSession, member_id: str) -> str | None:
        """Return the stable test member ref."""
        del session
        return "member:alice" if member_id == "member-1" else None

    async def team_ref(self, session: AsyncSession, team_id: str) -> str | None:
        """Implement the unused team lookup."""
        del session, team_id
        return None

    async def team_refs_of(self, session: AsyncSession, member_id: str) -> tuple[str, ...]:
        """Implement the unused member-team lookup."""
        del session, member_id
        return ()

    async def member_for_owner(self, session: AsyncSession, owner_ref: str) -> str | None:
        """Implement the unused owner lookup."""
        del session, owner_ref
        return None

    async def team_lead(self, session: AsyncSession, team_slug: str) -> str | None:
        """Implement the unused team lead lookup."""
        del session, team_slug
        return None


@dataclass
class RecordingSmtp:
    """Record email delivery without network access."""

    messages: list[EmailMessage]

    def send(self, message: EmailMessage) -> None:
        """Capture one email message."""
        self.messages.append(message)


async def test_guard_returns_only_a_block_verdict_and_hit_count() -> None:
    """Report a detected secret as a count without returning the raw value."""
    raw_sample: str = github_token().value
    guard: DlpOutboundGuard = DlpOutboundGuard(make_gate(DlpPolicy.BLOCK))
    verdict: GuardVerdict = await guard.inspect(f"report contains {raw_sample}")
    assert verdict.allowed is False
    assert verdict.hit_count >= 1
    assert raw_sample not in repr(verdict)
    clean: GuardVerdict = await guard.inspect("No detected content.")
    assert clean.allowed is True
    assert clean.hit_count == 0


@pytest.mark.parametrize(
    ("response_body", "slack_user_id", "expected_ok", "expected_error"),
    (
        ('{"ok":true,"channel":"D12345678","ts":"1"}', "U12345678", True, None),
        ('{"ok":false,"error":"invalid_auth"}', "U12345678", False, ErrorCode.DELIVERY_FAILED),
        ('{"ok":true}', None, False, ErrorCode.RECIPIENT_UNREACHABLE),
    ),
)
async def test_notification_tester_reports_transport_outcome(
    response_body: str,
    slack_user_id: str | None,
    expected_ok: bool,
    expected_error: ErrorCode | None,
) -> None:
    """Queue and inline deliver one test notification, returning only its safe outcome."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: RecordingAudit = RecordingAudit()
    queue: RecordingQueue = RecordingQueue()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    resolver: StaticResolver = StaticResolver(
        (
            (
                "member:alice",
                ResolvedRecipients(
                    ("alice@example.test",) if slack_user_id is not None else (),
                    (),
                    (slack_user_id,) if slack_user_id is not None else (),
                    ("member-1",),
                ),
            ),
        )
    )
    service: NotificationService = NotificationService(
        make_gate(DlpPolicy.MASK), resolver, queue, audit, clock, NotificationRepository()
    )
    async with harness.sessions.begin() as session:
        session.add(member_row("member-1", "alice", "alice@example.test", slack_user_id))

    def slack_response(request: httpx.Request) -> httpx.Response:
        """Return a fixture response without inspecting or recording message content."""
        del request
        return httpx.Response(200, text=response_body)

    client: httpx.AsyncClient = httpx.AsyncClient(
        transport=httpx.MockTransport(slack_response), base_url="https://slack.test"
    )
    smtp: RecordingSmtp = RecordingSmtp([])
    delivery: NotificationDeliveryHandler = NotificationDeliveryHandler(
        harness.sessions,
        resolver,
        SlackNotifier(client, SecretValue("test-token"), "https://slack.test"),
        EmailNotifier(smtp, "notifications@example.test"),
        audit,
        clock,
        NotificationRepository(),
    )
    tester: NotificationServiceTester = NotificationServiceTester(
        harness.sessions, service, delivery, FakeMembers()
    )
    outcome: NotificationTestOutcome = await tester.send_test(NotificationChannel.SLACK, "member-1")
    assert outcome.ok is expected_ok
    assert outcome.error_code is expected_error
    assert outcome.notification_id is not None
    await client.aclose()
    await harness.close()
