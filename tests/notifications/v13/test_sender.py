"""Category-aware notification filtering and member-send behavior tests."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import (
    DlpPolicy,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
    NotificationPreference,
    NotificationStatus,
)
from telescreen.db.models.notifications import NotificationRow
from telescreen.notifications.preferences import PreferenceRepository, PreferenceService
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.ports.directory import ResolvedRecipients
from telescreen.ports.notifications import (
    MemberNotification,
    NotificationContext,
    NotificationReceipt,
    NotificationRequest,
)
from tests.notifications.v12.fakes import make_gate
from tests.notifications.v13.fakes import RecordingAudit, RecordingQueue, StaticResolver, member_row
from tests.support.db import SqliteHarness, make_sqlite_harness


@pytest.fixture
async def sender_world() -> tuple[SqliteHarness, NotificationService, RecordingQueue]:
    """Build a SQLite sender with member and team recipient resolutions."""
    harness: SqliteHarness = await make_sqlite_harness()
    queue: RecordingQueue = RecordingQueue()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    resolver: StaticResolver = StaticResolver(
        (
            (
                "member:alice",
                ResolvedRecipients(("alice@example.test",), (), ("U12345678",), ("member-1",)),
            ),
            (
                "team:secops",
                ResolvedRecipients(("secops@example.test",), ("C12345678",), (), ()),
            ),
        )
    )
    service: NotificationService = NotificationService(
        make_gate(DlpPolicy.MASK),
        resolver,
        queue,
        RecordingAudit(),
        clock,
        NotificationRepository(),
    )
    async with harness.sessions.begin() as session:
        session.add(member_row("member-1", "alice", "alice@example.test", "U12345678"))
    return harness, service, queue


async def test_alert_filter_skips_member_and_keeps_team_target(
    sender_world: tuple[SqliteHarness, NotificationService, RecordingQueue],
) -> None:
    """Skip only the member whose stored channel differs from the alert target."""
    harness: SqliteHarness
    service: NotificationService
    queue: RecordingQueue
    harness, service, queue = sender_world
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    preferences: PreferenceService = PreferenceService(PreferenceRepository(), clock)
    async with harness.sessions.begin() as session:
        await preferences.update(
            session, "member-1", ((NotificationCategory.ALERTS, NotificationPreference.EMAIL),)
        )
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send(
            session,
            NotificationRequest(
                NotificationChannel.SLACK,
                ("member:alice", "team:secops"),
                "",
                "",
                "system:alerts",
                event=NotificationEvent.ALERT_FIRED,
                category=NotificationCategory.ALERTS,
            ),
        )
    assert len(receipt.skipped) == 1
    assert len(receipt.notification_ids) == 1
    assert len(queue.specs) == 1
    async with harness.sessions() as session:
        rows: tuple[NotificationRow, ...] = tuple(
            (
                await session.scalars(
                    select(NotificationRow).order_by(NotificationRow.recipient_ref)
                )
            ).all()
        )
    assert rows[0].recipient_ref == "member:alice"
    assert rows[0].status == NotificationStatus.SKIPPED.value
    assert rows[0].error_code == "recipient_opted_out"
    assert rows[0].category == NotificationCategory.ALERTS.value
    assert rows[1].recipient_ref == "team:secops"
    assert rows[1].status == NotificationStatus.QUEUED.value
    assert rows[1].category == NotificationCategory.ALERTS.value
    await harness.close()


async def test_alert_email_channel_queues_member(
    sender_world: tuple[SqliteHarness, NotificationService, RecordingQueue],
) -> None:
    """Deliver an alert to the member when the configured and requested channels match."""
    harness: SqliteHarness
    service: NotificationService
    queue: RecordingQueue
    harness, service, queue = sender_world
    preferences: PreferenceService = PreferenceService(
        PreferenceRepository(), FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    )
    async with harness.sessions.begin() as session:
        await preferences.update(
            session, "member-1", ((NotificationCategory.ALERTS, NotificationPreference.EMAIL),)
        )
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send(
            session,
            NotificationRequest(
                NotificationChannel.EMAIL,
                ("member:alice",),
                "",
                "",
                "system:alerts",
                event=NotificationEvent.ALERT_FIRED,
                category=NotificationCategory.ALERTS,
            ),
        )
    assert receipt.queued
    assert len(queue.specs) == 1
    async with harness.sessions() as session:
        row: NotificationRow = (await session.scalars(select(NotificationRow))).one()
    assert row.status == NotificationStatus.QUEUED.value
    await harness.close()


async def test_workflow_opt_out_and_sla_escalation_bypass(
    sender_world: tuple[SqliteHarness, NotificationService, RecordingQueue],
) -> None:
    """Filter human tasks but never suppress an SLA escalation."""
    harness: SqliteHarness
    service: NotificationService
    queue: RecordingQueue
    harness, service, queue = sender_world
    preferences: PreferenceService = PreferenceService(
        PreferenceRepository(), FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    )
    async with harness.sessions.begin() as session:
        await preferences.update(
            session,
            "member-1",
            (
                (NotificationCategory.WORKFLOW_TASKS, NotificationPreference.OFF),
                (NotificationCategory.ALERTS, NotificationPreference.OFF),
                (NotificationCategory.ASSIGNED, NotificationPreference.OFF),
                (NotificationCategory.MENTIONS, NotificationPreference.OFF),
                (NotificationCategory.SUBSCRIBED, NotificationPreference.OFF),
                (NotificationCategory.WEEKLY_REPORT, NotificationPreference.OFF),
            ),
        )
    async with harness.sessions.begin() as session:
        task_receipt: NotificationReceipt = await service.send(
            session,
            NotificationRequest(
                NotificationChannel.EMAIL,
                ("member:alice",),
                "",
                "",
                "system:workflow",
                event=NotificationEvent.HUMAN_TASK_ASSIGNED,
                context=NotificationContext(task_title="Review"),
            ),
        )
    assert task_receipt.skipped
    async with harness.sessions.begin() as session:
        sla_receipt: NotificationReceipt = await service.send(
            session,
            NotificationRequest(
                NotificationChannel.EMAIL,
                ("member:alice",),
                "",
                "",
                "system:workflow",
                event=NotificationEvent.SLA_ESCALATED,
                context=NotificationContext(task_title="Escalation"),
                category=NotificationCategory.WORKFLOW_TASKS,
            ),
        )
    assert sla_receipt.queued
    assert len(queue.specs) == 1
    async with harness.sessions() as session:
        rows: tuple[NotificationRow, ...] = tuple(
            (
                await session.scalars(select(NotificationRow).order_by(NotificationRow.created_at))
            ).all()
        )
    assert rows[0].error_code == "recipient_opted_out"
    assert rows[0].category == NotificationCategory.WORKFLOW_TASKS.value
    assert rows[1].status == NotificationStatus.QUEUED.value
    assert rows[1].category == NotificationCategory.WORKFLOW_TASKS.value
    await harness.close()


async def test_send_to_member_uses_member_preference(
    sender_world: tuple[SqliteHarness, NotificationService, RecordingQueue],
) -> None:
    """Choose a personal Slack channel for a member with Slack-only preference."""
    harness: SqliteHarness
    service: NotificationService
    queue: RecordingQueue
    harness, service, queue = sender_world
    preferences: PreferenceService = PreferenceService(
        PreferenceRepository(), FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    )
    async with harness.sessions.begin() as session:
        await preferences.update(
            session, "member-1", ((NotificationCategory.MENTIONS, NotificationPreference.SLACK),)
        )
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send_to_member(
            session,
            MemberNotification(
                "member-1",
                NotificationCategory.MENTIONS,
                NotificationEvent.ISSUE_MENTIONED,
                NotificationContext(activity_actor="Alice"),
                "issue-1",
            ),
        )
    assert receipt.queued
    async with harness.sessions() as session:
        row: NotificationRow = (await session.scalars(select(NotificationRow))).one()
    assert row.channel == NotificationChannel.SLACK.value
    assert row.category == NotificationCategory.MENTIONS.value
    assert row.issue_id == "issue-1"
    assert len(queue.specs) == 1
    await harness.close()


async def test_send_to_member_off_records_skip_without_delivery_task(
    sender_world: tuple[SqliteHarness, NotificationService, RecordingQueue],
) -> None:
    """Record an opted-out personal notification without enqueuing delivery."""
    harness: SqliteHarness
    service: NotificationService
    queue: RecordingQueue
    harness, service, queue = sender_world
    preferences: PreferenceService = PreferenceService(
        PreferenceRepository(), FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    )
    async with harness.sessions.begin() as session:
        await preferences.update(
            session, "member-1", ((NotificationCategory.MENTIONS, NotificationPreference.OFF),)
        )
    async with harness.sessions.begin() as session:
        receipt: NotificationReceipt = await service.send_to_member(
            session,
            MemberNotification(
                "member-1",
                NotificationCategory.MENTIONS,
                NotificationEvent.ISSUE_MENTIONED,
                NotificationContext(activity_actor="Alice"),
            ),
        )
    assert receipt.skipped
    assert not receipt.notification_ids
    assert not queue.specs
    async with harness.sessions() as session:
        row: NotificationRow = (await session.scalars(select(NotificationRow))).one()
    assert row.status == NotificationStatus.SKIPPED.value
    assert row.error_code == "recipient_opted_out"
    assert row.category == NotificationCategory.MENTIONS.value
    await harness.close()
