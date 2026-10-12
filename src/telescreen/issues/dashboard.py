"""Dashboard aggregates and weekly issue report source."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.vocabulary import UNRESOLVED_STATUSES, IssueStatus
from telescreen.db.models import FindingRow, IssueHistoryRow, IssueRow
from telescreen.issues.keys import short_id
from telescreen.issues.query.period import Period
from telescreen.ports.issues import IssueReportSource, ReportIssue, WeeklySummary


@dataclass(frozen=True)
class DashboardBucket:
    """One hour or day of issue and finding activity."""

    start: datetime
    critical: int
    high: int
    medium: int
    new_issues: int
    resolved_issues: int


@dataclass(frozen=True)
class DashboardIssue:
    """A ranked issue with only its masked title and safe identifiers."""

    id: str
    short_id: str
    display_title: str
    findings: int


@dataclass(frozen=True)
class DashboardData:
    """Count-only overview data for the selected period."""

    buckets: tuple[DashboardBucket, ...]
    open_by_exposure: tuple[tuple[str, int], ...]
    open_by_source: tuple[tuple[str, int], ...]
    archived_count: int
    escalating_count: int
    top_issues: tuple[DashboardIssue, ...]
    mttr_seconds: int | None
    mtta_seconds: int | None
    unassigned_high_priority: int


@dataclass(frozen=True)
class DashboardService:
    """Compute dashboard aggregates without returning unmasked issue fields."""

    async def dashboard(
        self, session: AsyncSession, period: Period, now: datetime
    ) -> DashboardData:
        """Return dashboard stats, excluding merged children from issue totals."""
        now_utc: datetime = ensure_utc(now)
        start: datetime = (
            ensure_utc(period.start) if period.start is not None else now_utc - timedelta(days=14)
        )
        end: datetime = ensure_utc(period.end) if period.end is not None else now_utc
        hour: bool = end - start <= timedelta(hours=24)
        buckets: tuple[DashboardBucket, ...] = await self._buckets(session, start, end, hour)
        statuses: tuple[str, ...] = tuple(item.value for item in UNRESOLVED_STATUSES)
        open_rows: tuple[tuple[str, int], ...] = tuple(
            (str(key or "unknown"), int(count))
            for key, count in (
                await session.execute(
                    select(IssueRow.exposure_visibility, func.count())
                    .where(IssueRow.status.in_(statuses), IssueRow.merged_into_id.is_(None))
                    .group_by(IssueRow.exposure_visibility)
                    .order_by(IssueRow.exposure_visibility)
                )
            ).all()
        )
        source_rows: tuple[tuple[str, int], ...] = tuple(
            (str(key or "unknown"), int(count))
            for key, count in (
                await session.execute(
                    select(IssueRow.source, func.count())
                    .where(IssueRow.status.in_(statuses), IssueRow.merged_into_id.is_(None))
                    .group_by(IssueRow.source)
                    .order_by(IssueRow.source)
                )
            ).all()
        )
        archived: int = int(
            await session.scalar(
                select(func.count())
                .select_from(IssueRow)
                .where(
                    IssueRow.status == IssueStatus.ARCHIVED.value,
                    IssueRow.merged_into_id.is_(None),
                )
            )
            or 0
        )
        escalating: int = int(
            await session.scalar(
                select(func.count())
                .select_from(IssueRow)
                .where(
                    IssueRow.status == IssueStatus.ESCALATING.value,
                    IssueRow.merged_into_id.is_(None),
                )
            )
            or 0
        )
        top_issues: tuple[DashboardIssue, ...] = await self._top_issues(session, start, end)
        mttr: int | None = await self._mean_resolution(session, start, end)
        mtta: int | None = await self._mean_acknowledgement(session, start, end)
        unassigned: int = int(
            await session.scalar(
                select(func.count())
                .select_from(IssueRow)
                .where(
                    IssueRow.status.in_(statuses),
                    IssueRow.merged_into_id.is_(None),
                    IssueRow.assignee_member_id.is_(None),
                    IssueRow.assignee_team_id.is_(None),
                    IssueRow.priority == "high",
                )
            )
            or 0
        )
        return DashboardData(
            buckets,
            open_rows,
            source_rows,
            archived,
            escalating,
            top_issues,
            mttr,
            mtta,
            unassigned,
        )

    async def _buckets(
        self, session: AsyncSession, start: datetime, end: datetime, hourly: bool
    ) -> tuple[DashboardBucket, ...]:
        """Build zero-filled time buckets from finding and issue timestamps."""
        width: timedelta = timedelta(hours=1) if hourly else timedelta(days=1)
        origin: datetime = (
            start.replace(minute=0, second=0, microsecond=0)
            if hourly
            else start.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        bucket_starts: list[datetime] = []
        cursor: datetime = origin
        while cursor < end:
            bucket_starts.append(cursor)
            cursor += width
        finding_rows: tuple[tuple[datetime, str, int], ...] = tuple(
            (row[0], row[1], int(row[2]))
            for row in (
                await session.execute(
                    select(FindingRow.observed_at, IssueRow.severity, func.count())
                    .join(IssueRow, IssueRow.id == FindingRow.issue_id)
                    .where(
                        FindingRow.observed_at >= start,
                        FindingRow.observed_at < end,
                        IssueRow.merged_into_id.is_(None),
                    )
                    .group_by(FindingRow.observed_at, IssueRow.severity)
                )
            ).all()
        )
        new_rows: tuple[datetime, ...] = tuple(
            (
                await session.scalars(
                    select(IssueRow.first_seen).where(
                        IssueRow.first_seen >= start,
                        IssueRow.first_seen < end,
                        IssueRow.merged_into_id.is_(None),
                    )
                )
            ).all()
        )
        resolved_values: tuple[datetime | None, ...] = tuple(
            (
                await session.scalars(
                    select(IssueRow.resolved_at).where(
                        IssueRow.resolved_at.is_not(None),
                        IssueRow.resolved_at >= start,
                        IssueRow.resolved_at < end,
                        IssueRow.merged_into_id.is_(None),
                    )
                )
            ).all()
        )
        resolved_rows: tuple[datetime, ...] = tuple(
            value for value in resolved_values if value is not None
        )
        critical_counts: list[int] = [0] * len(bucket_starts)
        high_counts: list[int] = [0] * len(bucket_starts)
        medium_counts: list[int] = [0] * len(bucket_starts)
        for observed, severity, count in finding_rows:
            index: int = self._index(ensure_utc(observed), origin, width)
            if severity == "critical":
                critical_counts[index] += count
            elif severity == "high":
                high_counts[index] += count
            elif severity == "medium":
                medium_counts[index] += count
        new_counts: list[int] = [0] * len(bucket_starts)
        for seen in new_rows:
            new_counts[self._index(ensure_utc(seen), origin, width)] += 1
        resolved_counts: list[int] = [0] * len(bucket_starts)
        for resolved in resolved_rows:
            resolved_counts[self._index(ensure_utc(resolved), origin, width)] += 1
        return tuple(
            DashboardBucket(
                at,
                critical_counts[index],
                high_counts[index],
                medium_counts[index],
                new_counts[index],
                resolved_counts[index],
            )
            for index, at in enumerate(bucket_starts)
        )

    def _index(self, value: datetime, origin: datetime, width: timedelta) -> int:
        """Return the selected bucket index for a UTC timestamp."""
        return int((value - origin) // width)

    async def _top_issues(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> tuple[DashboardIssue, ...]:
        """Return the ten issues with most findings in the time window."""
        rows: tuple[tuple[IssueRow, int], ...] = tuple(
            (
                row[0],
                int(row[1]),
            )
            for row in (
                await session.execute(
                    select(IssueRow, func.count(FindingRow.id).label("finding_count"))
                    .join(FindingRow, FindingRow.issue_id == IssueRow.id)
                    .where(
                        FindingRow.observed_at >= start,
                        FindingRow.observed_at < end,
                        IssueRow.merged_into_id.is_(None),
                    )
                    .group_by(IssueRow.id)
                    .order_by(func.count(FindingRow.id).desc(), IssueRow.id)
                    .limit(10)
                )
            ).all()
        )
        return tuple(
            DashboardIssue(issue.id, short_id(issue.number), issue.display_title, count)
            for issue, count in rows
        )

    async def _mean_resolution(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> int | None:
        """Return integer mean time to resolve, or null when there is no data."""
        values: tuple[tuple[datetime, datetime], ...] = tuple(
            (row[0], row[1])
            for row in (
                await session.execute(
                    select(IssueRow.first_seen, IssueRow.resolved_at).where(
                        IssueRow.resolved_at.is_not(None),
                        IssueRow.resolved_at >= start,
                        IssueRow.resolved_at < end,
                        IssueRow.merged_into_id.is_(None),
                    )
                )
            ).all()
        )
        if not values:
            return None
        return int(
            sum(
                (ensure_utc(resolved) - ensure_utc(first)).total_seconds()
                for first, resolved in values
            )
            / len(values)
        )

    async def _mean_acknowledgement(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> int | None:
        """Return mean first human response latency for issues first seen in range."""
        issue_rows: tuple[IssueRow, ...] = tuple(
            (
                await session.scalars(
                    select(IssueRow).where(
                        IssueRow.first_seen >= start,
                        IssueRow.first_seen < end,
                        IssueRow.merged_into_id.is_(None),
                    )
                )
            ).all()
        )
        latencies: list[int] = []
        for issue in issue_rows:
            first: datetime | None = await session.scalar(
                select(func.min(IssueHistoryRow.at)).where(
                    IssueHistoryRow.issue_id == issue.id,
                    IssueHistoryRow.kind.in_(("status_changed", "assigned", "commented")),
                    ~IssueHistoryRow.actor_ref.startswith("system:"),
                )
            )
            if first is not None:
                latencies.append(
                    int((ensure_utc(first) - ensure_utc(issue.first_seen)).total_seconds())
                )
        return int(sum(latencies) / len(latencies)) if latencies else None


@dataclass(frozen=True)
class IssueReportSourceImpl(IssueReportSource):
    """Implement weekly report summaries from masked issue records."""

    async def weekly_summary(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> WeeklySummary:
        """Return weekly counts, mean resolution time and five safe top issues."""
        lower: datetime = ensure_utc(start)
        upper: datetime = ensure_utc(end)
        service: DashboardService = DashboardService()
        new_count: int = int(
            await session.scalar(
                select(func.count())
                .select_from(IssueRow)
                .where(
                    IssueRow.first_seen >= lower,
                    IssueRow.first_seen < upper,
                    IssueRow.merged_into_id.is_(None),
                )
            )
            or 0
        )
        resolved_count: int = int(
            await session.scalar(
                select(func.count())
                .select_from(IssueRow)
                .where(
                    IssueRow.resolved_at >= lower,
                    IssueRow.resolved_at < upper,
                    IssueRow.merged_into_id.is_(None),
                )
            )
            or 0
        )
        regressed_count: int = int(
            await session.scalar(
                select(func.count())
                .select_from(IssueHistoryRow)
                .where(
                    IssueHistoryRow.kind == "regressed",
                    IssueHistoryRow.at >= lower,
                    IssueHistoryRow.at < upper,
                )
            )
            or 0
        )
        escalating_count: int = int(
            await session.scalar(
                select(func.count())
                .select_from(IssueHistoryRow)
                .where(
                    IssueHistoryRow.kind == "escalated",
                    IssueHistoryRow.at >= lower,
                    IssueHistoryRow.at < upper,
                )
            )
            or 0
        )
        open_count: int = int(
            await session.scalar(
                select(func.count())
                .select_from(IssueRow)
                .where(
                    IssueRow.status.in_(tuple(item.value for item in UNRESOLVED_STATUSES)),
                    IssueRow.merged_into_id.is_(None),
                )
            )
            or 0
        )
        mttr_seconds: int | None = await service._mean_resolution(session, lower, upper)
        issues: tuple[DashboardIssue, ...] = await service._top_issues(session, lower, upper)
        top: tuple[ReportIssue, ...] = tuple(
            ReportIssue(issue.id, issue.short_id, issue.display_title, issue.findings)
            for issue in issues[:5]
        )
        return WeeklySummary(
            lower,
            upper,
            new_count,
            resolved_count,
            regressed_count,
            escalating_count,
            open_count,
            mttr_seconds,
            top,
        )

    async def assigned_open_count(self, session: AsyncSession, member_id: str) -> int:
        """Count unresolved, unmerged issues currently assigned to the member."""
        return int(
            await session.scalar(
                select(func.count())
                .select_from(IssueRow)
                .where(
                    IssueRow.assignee_member_id == member_id,
                    IssueRow.status.in_(tuple(item.value for item in UNRESOLVED_STATUSES)),
                    IssueRow.merged_into_id.is_(None),
                )
            )
            or 0
        )
