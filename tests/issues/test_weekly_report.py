"""Weekly report scheduling, fan-out and masking tests."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.json import JsonValue, dump_json, json_object, json_str
from telescreen.core.vocabulary import (
    DlpPolicy,
    EngineTaskKind,
    NotificationCategory,
    NotificationPreference,
    NotificationStatus,
)
from telescreen.db.models.notifications import NotificationRow
from telescreen.issues.weekly_report import WeeklyReportHandler, WeeklyReportProvider
from telescreen.notifications.preferences import PreferenceRepository, PreferenceService
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.ports.directory import ResolvedRecipients
from telescreen.ports.issues import IssueReportSource, ReportIssue, WeeklySummary
from telescreen.ports.workflow import ClaimedTask, TaskResult
from tests.notifications.v12.fakes import make_gate
from tests.notifications.v13.fakes import RecordingAudit, RecordingQueue, StaticResolver, member_row
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import assert_no_leaks, github_token


async def test_weekly_provider_enqueues_once_at_fire_and_empty_cron_disables() -> None:
    """Use a weekly fire minute as an idempotency key and honor disabled schedules."""
    harness: SqliteHarness = await make_sqlite_harness()
    queue: RecordingQueue = RecordingQueue()
    provider: WeeklyReportProvider = WeeklyReportProvider("0 9 * * 1", queue)
    fire: datetime = datetime(2026, 10, 12, 9, tzinfo=UTC)
    async with harness.sessions.begin() as session:
        assert await provider.tick(session, fire) == 1
    async with harness.sessions.begin() as session:
        next_minute: datetime = datetime(2026, 10, 12, 9, 1, tzinfo=UTC)
        assert await provider.tick(session, next_minute) == 0
    assert len(queue.specs) == 1
    assert queue.specs[0].kind is EngineTaskKind.WEEKLY_REPORT
    assert queue.specs[0].idempotency_key == f"weekly:{int(fire.timestamp()) // 60}"
    disabled: WeeklyReportProvider = WeeklyReportProvider(None, queue)
    async with harness.sessions.begin() as session:
        assert await disabled.tick(session, fire) == 0
    await harness.close()


@dataclass(frozen=True)
class StaticReportSource(IssueReportSource):
    """Return a fixed safe-shape report and member assigned counts."""

    summary: WeeklySummary
    assigned_calls: list[str]
    summary_calls: list[tuple[datetime, datetime]]

    async def weekly_summary(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> WeeklySummary:
        """Return the prepared summary once for the tested period."""
        del session
        self.summary_calls.append((start, end))
        return self.summary

    async def assigned_open_count(self, session: AsyncSession, member_id: str) -> int:
        """Record which members received an assigned-open lookup."""
        del session
        self.assigned_calls.append(member_id)
        return 3


async def test_weekly_handler_masks_summary_and_sends_active_members() -> None:
    """Render masked summary text, skip an opted-out member, and exclude inactive users."""
    harness: SqliteHarness = await make_sqlite_harness()
    raw_sample: str = github_token().value
    queue: RecordingQueue = RecordingQueue()
    audit: RecordingAudit = RecordingAudit()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, 9, tzinfo=UTC))
    resolver: StaticResolver = StaticResolver(
        (
            ("member:alice", ResolvedRecipients(("alice@example.test",), (), (), ("member-1",))),
            ("member:bob", ResolvedRecipients(("bob@example.test",), (), (), ("member-2",))),
        )
    )
    service: NotificationService = NotificationService(
        make_gate(DlpPolicy.MASK), resolver, queue, audit, clock, NotificationRepository()
    )
    async with harness.sessions.begin() as session:
        session.add(member_row("member-1", "alice", "alice@example.test", None))
        session.add(member_row("member-2", "bob", "bob@example.test", None))
        session.add(member_row("member-3", "inactive", "inactive@example.test", None, False))
    preferences: PreferenceService = PreferenceService(PreferenceRepository(), clock)
    async with harness.sessions.begin() as session:
        await preferences.update(
            session, "member-2", ((NotificationCategory.WEEKLY_REPORT, NotificationPreference.OFF),)
        )
    summary: WeeklySummary = WeeklySummary(
        datetime(2026, 10, 5, 9, tzinfo=UTC),
        datetime(2026, 10, 12, 9, tzinfo=UTC),
        4,
        2,
        1,
        3,
        8,
        5400,
        (ReportIssue("issue-1", "TELE-42", raw_sample, 9),),
    )
    source: StaticReportSource = StaticReportSource(summary, [], [])
    handler: WeeklyReportHandler = WeeklyReportHandler(harness.sessions, source, service, clock)
    payload: JsonValue = queue_payload(
        datetime(2026, 10, 5, 9, tzinfo=UTC), datetime(2026, 10, 12, 9, tzinfo=UTC)
    )
    task: ClaimedTask = ClaimedTask(
        "task-1",
        EngineTaskKind.WEEKLY_REPORT,
        "weekly:29764860",
        payload,
        1,
        3,
        "weekly:29764860",
    )
    result: TaskResult = await handler.handle(task)
    assert result.error_code is None
    assert source.summary_calls == [
        (datetime(2026, 10, 5, 9, tzinfo=UTC), datetime(2026, 10, 12, 9, tzinfo=UTC))
    ]
    assert source.assigned_calls == ["member-1", "member-2"]
    assert len(queue.specs) == 1
    assert_no_leaks(dump_json(queue.specs[0].payload), "weekly task payload")
    async with harness.sessions() as session:
        rows: tuple[NotificationRow, ...] = tuple(
            (
                await session.scalars(
                    select(NotificationRow).order_by(NotificationRow.recipient_ref)
                )
            ).all()
        )
    assert len(rows) == 2
    assert rows[0].status == NotificationStatus.QUEUED.value
    assert rows[0].category == NotificationCategory.WEEKLY_REPORT.value
    assert rows[1].status == NotificationStatus.SKIPPED.value
    assert rows[1].error_code == "recipient_opted_out"
    for row in rows:
        assert_no_leaks(row.subject + row.body, "weekly report notification")
    await harness.close()


def queue_payload(start: datetime, end: datetime) -> JsonValue:
    """Build a weekly task payload with fire-window bounds."""
    return json_object(
        (("start_at", json_str(start.isoformat())), ("end_at", json_str(end.isoformat())))
    )
