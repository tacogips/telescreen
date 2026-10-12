"""Scheduled weekly masked issue report generation and delivery."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.json import JsonValue, json_object, json_str
from telescreen.core.vocabulary import (
    EngineTaskKind,
    ErrorCode,
    NotificationCategory,
    NotificationEvent,
)
from telescreen.db.models import MemberRow
from telescreen.ports.issues import IssueReportSource, WeeklySummary
from telescreen.ports.notifications import (
    MemberNotification,
    MemberNotifier,
    NotificationContext,
)
from telescreen.ports.workflow import (
    ClaimedTask,
    ScheduleProvider,
    TaskHandler,
    TaskQueue,
    TaskResult,
    TaskSpec,
)


@dataclass
class WeeklyReportProvider(ScheduleProvider):
    """Enqueue each recent cron fire once by its UTC minute."""

    cron: str | None
    queue: TaskQueue
    last_tick_at: datetime | None = None

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Enqueue a weekly task when its most recent cron fire is within one hour."""
        current: datetime = ensure_utc(now)
        if self.last_tick_at is not None and current - self.last_tick_at < timedelta(seconds=60):
            return 0
        self.last_tick_at = current
        if self.cron is None:
            return 0
        fire: datetime = (
            current
            if croniter.match(self.cron, current)
            else ensure_utc(croniter(self.cron, current).get_prev(datetime))
        )
        age: timedelta = current - fire
        if age < timedelta(0) or age > timedelta(hours=1):
            return 0
        previous: datetime = ensure_utc(croniter(self.cron, fire).get_prev(datetime))
        fire_minute: int = int(fire.timestamp()) // 60
        payload: JsonValue = json_object(
            (("start_at", json_str(previous.isoformat())), ("end_at", json_str(fire.isoformat())))
        )
        queued: bool = await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.WEEKLY_REPORT,
                f"weekly:{fire_minute}",
                payload,
                current,
                f"weekly:{fire_minute}",
            ),
        )
        return int(queued)


@dataclass(frozen=True)
class WeeklyReportHandler(TaskHandler):
    """Build one masked summary and send it in isolated member transactions."""

    sessions: async_sessionmaker[AsyncSession]
    source: IssueReportSource
    notifier: MemberNotifier
    clock: Clock
    kind: EngineTaskKind = EngineTaskKind.WEEKLY_REPORT

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Send a weekly report to each active member and continue after failures."""
        start: datetime | None = _payload_time(task.payload, "start_at")
        end: datetime | None = _payload_time(task.payload, "end_at")
        if start is None or end is None or start >= end:
            return TaskResult.dead(ErrorCode.VALIDATION_ERROR)
        async with self.sessions() as session:
            summary: WeeklySummary = await self.source.weekly_summary(session, start, end)
            member_ids: tuple[str, ...] = tuple(
                (
                    await session.scalars(
                        select(MemberRow.id)
                        .where(MemberRow.active.is_(True))
                        .order_by(MemberRow.id)
                    )
                ).all()
            )
        report_summary: str = _summary_text(summary)
        failed: bool = False
        for member_id in member_ids:
            try:
                async with self.sessions.begin() as session:
                    assigned_open: int = await self.source.assigned_open_count(session, member_id)
                    await self.notifier.send_to_member(
                        session,
                        MemberNotification(
                            member_id,
                            NotificationCategory.WEEKLY_REPORT,
                            NotificationEvent.WEEKLY_REPORT,
                            NotificationContext(
                                report_summary=report_summary,
                                report_assigned_open=str(assigned_open),
                            ),
                        ),
                    )
            except Exception:
                failed = True
        return TaskResult.dead(ErrorCode.DELIVERY_FAILED) if failed else TaskResult.done()


def _payload_time(payload: JsonValue, name: str) -> datetime | None:
    """Parse one timestamp from the task's closed JSON payload."""
    value: JsonValue | None = payload.get(name)
    text: str | None = value.as_str() if value is not None else None
    if text is None:
        return None
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _summary_text(summary: WeeklySummary) -> str:
    """Format only counts, masked issue titles, and short issue identifiers."""
    hours: str = "not available"
    if summary.mttr_seconds is not None:
        hours = f"{summary.mttr_seconds / 3600:.1f}"
    lines: list[str] = [
        f"New: {summary.new_count}",
        f"Resolved: {summary.resolved_count}",
        f"Regressed: {summary.regressed_count}",
        f"Escalating: {summary.escalating_count}",
        f"Open: {summary.open_count}",
        f"MTTR hours: {hours}",
        "Top issues:",
    ]
    for issue in summary.top_issues[:5]:
        lines.append(f"{issue.short_id} {issue.display_title} ({issue.findings})")
    return "\n".join(lines)
