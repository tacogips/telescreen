"""Delivery pump leasing, cutover seeding, retries, and terminal results."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.vocabulary import NotificationStatus
from telescreen.db.models import NotificationDeliveryStateRow, NotificationRow
from telescreen.db.ownership import Store
from telescreen.notification_service.worker import DeliveryPump
from telescreen.notifications.delivery import NotificationDeliveryHandler
from telescreen.notifications.email import EmailNotifier
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.slack import SlackNotifier
from telescreen.ports.directory import ResolvedRecipients
from tests.notifications.v13.fakes import RecordingAudit, StaticResolver
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class RecordingSmtp:
    """Record messages or simulate an unavailable SMTP transport."""

    fail: bool
    messages: list[EmailMessage] = field(default_factory=list)

    def send(self, message: EmailMessage) -> None:
        """Record one message or raise a safe transport error."""
        if self.fail:
            raise OSError("SMTP unavailable")
        self.messages.append(message)


@dataclass(frozen=True)
class _FailOncePump(DeliveryPump):
    """Raise once, then signal that a later delivery tick ran."""

    ticks: list[int] = field(default_factory=list)
    recovered: asyncio.Event = field(default_factory=asyncio.Event)

    async def tick(self) -> int:
        """Fail on the first poll and signal recovery on the next poll."""
        self.ticks.append(len(self.ticks) + 1)
        if len(self.ticks) == 1:
            raise RuntimeError("sensitive sentinel")
        self.recovered.set()
        return 0


async def test_delivery_pump_continues_after_tick_error_and_cancels(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A transient tick error is logged safely and does not stop the pump."""
    harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    now: datetime = datetime(2026, 10, 16, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    resolver: StaticResolver = StaticResolver(())
    handler: NotificationDeliveryHandler = NotificationDeliveryHandler(
        harness.sessions,
        resolver,
        SlackNotifier(httpx.AsyncClient(), None),
        EmailNotifier(RecordingSmtp(False), "sender@example.test"),
        RecordingAudit(),
        clock,
        NotificationRepository(),
    )
    pump: _FailOncePump = _FailOncePump(harness.sessions, handler, clock, poll_seconds=0)
    task: asyncio.Task[None] = asyncio.create_task(pump.run())
    try:
        with caplog.at_level(logging.WARNING, logger="telescreen.notification_service.worker"):
            await asyncio.wait_for(pump.recovered.wait(), timeout=1.0)
        assert len(pump.ticks) >= 2
        assert not task.done()
        failure_logs: list[logging.LogRecord] = [
            record
            for record in caplog.records
            if record.name == "telescreen.notification_service.worker"
        ]
        assert any(
            record.getMessage() == "notification delivery tick failed type=RuntimeError"
            for record in failure_logs
        )
        assert all("sensitive sentinel" not in record.getMessage() for record in failure_logs)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await handler.slack.http.aclose()
        await harness.close()


@pytest.mark.parametrize("case", ("sent", "retry", "dead"))
async def test_delivery_pump_applies_result_and_seeds_missing_state(case: str) -> None:
    """Run the real handler through a pump for sent, retry, and dead outcomes."""
    harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    now: datetime = datetime(2026, 10, 16, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    recipient: str = "email:member@example.test"
    resolver: StaticResolver = StaticResolver(
        ((recipient, ResolvedRecipients(("member@example.test",), (), (), ())),)
    )
    smtp: RecordingSmtp = RecordingSmtp(case != "sent")
    handler: NotificationDeliveryHandler = NotificationDeliveryHandler(
        harness.sessions,
        resolver,
        SlackNotifier(httpx.AsyncClient(), None),
        EmailNotifier(smtp, "sender@example.test"),
        RecordingAudit(),
        clock,
        NotificationRepository(),
    )
    pump: DeliveryPump = DeliveryPump(harness.sessions, handler, clock)
    async with harness.sessions.begin() as session:
        session.add(
            NotificationRow(
                id=f"notification-{case}",
                channel="email",
                recipient_ref=recipient,
                subject="Notice",
                body="Safe body",
                dlp_verdict="pass",
                dlp_hit_count=0,
                status=NotificationStatus.QUEUED.value,
                delivery_attempts=0,
                run_id=None,
                issue_id=None,
                created_at=now,
                sent_at=None,
                event_kind=None,
                template_name=None,
                template_version=None,
                set_name=None,
                target_index=None,
                error_code=None,
                category=None,
            )
        )
        if case == "dead":
            session.add(
                NotificationDeliveryStateRow(
                    notification_id=f"notification-{case}",
                    attempt=0,
                    max_attempts=1,
                    available_at=now,
                    leased_until=None,
                )
            )
    assert await pump.tick() == 1
    async with harness.sessions() as session:
        row: NotificationRow = (
            await session.scalars(
                select(NotificationRow).where(NotificationRow.id == f"notification-{case}")
            )
        ).one()
        state: NotificationDeliveryStateRow | None = await session.get(
            NotificationDeliveryStateRow, f"notification-{case}"
        )
    if case == "sent":
        assert row.status == NotificationStatus.SENT.value
        assert len(smtp.messages) == 1
        assert state is None
    elif case == "retry":
        assert row.status == NotificationStatus.QUEUED.value
        assert state is not None
        assert state.attempt == 1
        assert ensure_utc(state.available_at) > now
        assert state.leased_until is None
    else:
        assert row.status == NotificationStatus.FAILED.value
        assert row.error_code == "delivery_failed"
        assert state is None
    await handler.slack.http.aclose()
    await harness.close()
