"""Dashboard and weekly report aggregate behavior."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute

from telescreen.api.common import ApiContext, PermissionDependency
from telescreen.api.routers.dashboard import DashboardRouterDeps, build_dashboard_router
from telescreen.core.clock import FakeClock
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import IssueStatus
from telescreen.db.models import IssueHistoryRow, IssueRow
from telescreen.issues.dashboard import DashboardData, DashboardService, IssueReportSourceImpl
from telescreen.issues.query.period import Period
from telescreen.ports.issues import WeeklySummary
from tests.issues.v13_lifecycle.test_archive import NOW, _finding, _issue
from tests.notifications.v13.fakes import RecordingAudit
from tests.support.db import SqliteHarness, make_sqlite_harness


@pytest.mark.asyncio
async def test_dashboard_route_declares_only_issues_read() -> None:
    """The direct dashboard route has exactly its one read permission."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    router: APIRouter = build_dashboard_router(
        DashboardRouterDeps(
            ApiContext(harness.sessions, clock, RecordingAudit([])), DashboardService()
        )
    )
    route: APIRoute = next(item for item in router.routes if isinstance(item, APIRoute))
    dependencies: list[PermissionDependency] = [
        dependency.call
        for dependency in route.dependant.dependencies
        if isinstance(dependency.call, PermissionDependency)
    ]
    assert len(dependencies) == 1
    assert dependencies[0].permission is Permission.ISSUES_READ
    await harness.close()


@pytest.mark.asyncio
async def test_dashboard_excludes_merged_children_and_averages_human_response() -> None:
    """Open totals and top issues exclude merged children; MTTA ignores system rows."""
    harness: SqliteHarness = await make_sqlite_harness()
    primary: IssueRow = _issue("dash-primary", 810)
    primary.priority = "high"
    child: IssueRow = _issue("dash-child", 811)
    child.merged_into_id = primary.id
    archived: IssueRow = _issue("dash-archived", 812, IssueStatus.ARCHIVED)
    archived.archived_at = NOW
    escalating: IssueRow = _issue("dash-escalating", 813, IssueStatus.ESCALATING)
    async with harness.sessions.begin() as session:
        session.add_all((primary, child, archived, escalating))
        session.add_all((_finding(primary.id, 810), _finding(child.id, 811)))
        session.add_all(
            (
                IssueHistoryRow(
                    id="sys-history",
                    issue_id=primary.id,
                    kind="commented",
                    actor_ref="system:worker",
                    at=NOW + timedelta(minutes=10),
                ),
                IssueHistoryRow(
                    id="human-history",
                    issue_id=primary.id,
                    kind="assigned",
                    actor_ref="member:triager",
                    at=NOW + timedelta(hours=1),
                ),
            )
        )
    async with harness.sessions() as session:
        result: DashboardData = await DashboardService().dashboard(
            session, Period(NOW, NOW + timedelta(days=1)), NOW
        )
        summary: WeeklySummary = await IssueReportSourceImpl().weekly_summary(
            session, NOW, NOW + timedelta(days=7)
        )
        assigned: int = await IssueReportSourceImpl().assigned_open_count(session, "missing-member")
    assert result.archived_count == 1
    assert result.escalating_count == 1
    assert result.unassigned_high_priority == 1
    assert result.mtta_seconds == 3600
    assert sum(bucket.critical + bucket.high + bucket.medium for bucket in result.buckets) == 1
    assert tuple(issue.id for issue in result.top_issues) == (primary.id,)
    assert summary.new_count == 3
    assert summary.open_count == 2
    assert summary.top_issues[0].display_title == "masked title"
    assert assigned == 0
    await harness.close()


@pytest.mark.asyncio
async def test_empty_dashboard_means_are_null_and_counts_zero() -> None:
    """No records yields null means and safe zero counts."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    async with harness.sessions() as session:
        result: DashboardData = await DashboardService().dashboard(
            session, Period(now - timedelta(days=14), now), now
        )
    assert result.mttr_seconds is None
    assert result.mtta_seconds is None
    assert result.archived_count == 0
    assert result.escalating_count == 0
    assert result.top_issues == ()
    await harness.close()


@pytest.mark.asyncio
async def test_dashboard_14d_daily_buckets_and_mttr() -> None:
    """Fourteen daily buckets count current severity and resolution time."""
    harness: SqliteHarness = await make_sqlite_harness()
    critical: IssueRow = _issue("dash-daily-critical", 820)
    critical.severity = "critical"
    critical.first_seen = NOW + timedelta(days=1)
    high: IssueRow = _issue("dash-daily-high", 821)
    high.first_seen = NOW + timedelta(days=3)
    resolved: IssueRow = _issue("dash-daily-resolved", 822, IssueStatus.RESOLVED)
    resolved.resolved_at = NOW + timedelta(days=2)
    async with harness.sessions.begin() as session:
        session.add_all((critical, high, resolved))
        session.add_all(
            (
                _finding(critical.id, 820, NOW + timedelta(days=1)),
                _finding(critical.id, 823, NOW + timedelta(days=1, minutes=1)),
                _finding(high.id, 821, NOW + timedelta(days=3)),
            )
        )
    async with harness.sessions() as session:
        result: DashboardData = await DashboardService().dashboard(
            session, Period(NOW, NOW + timedelta(days=14)), NOW + timedelta(days=14)
        )
    assert len(result.buckets) == 14
    assert result.buckets[1].start - result.buckets[0].start == timedelta(days=1)
    assert result.buckets[1].critical == 2
    assert result.buckets[3].high == 1
    assert result.buckets[2].resolved_issues == 1
    assert result.mttr_seconds == 172800
    await harness.close()


@pytest.mark.asyncio
async def test_weekly_summary_counts_ordering_and_assigned_open() -> None:
    """Weekly counts, MTTR, finding order and unresolved assignment are exact."""
    harness: SqliteHarness = await make_sqlite_harness()
    issue_a: IssueRow = _issue("weekly-a", 830)
    issue_a.assignee_member_id = "member-x"
    issue_b: IssueRow = _issue("weekly-b", 831)
    resolved: IssueRow = _issue("weekly-resolved", 832, IssueStatus.RESOLVED)
    resolved.assignee_member_id = "member-x"
    resolved.resolved_at = NOW + timedelta(hours=48)
    async with harness.sessions.begin() as session:
        session.add_all((issue_a, issue_b, resolved))
        session.add_all(
            (
                _finding(issue_a.id, 830),
                _finding(issue_a.id, 833),
                _finding(issue_a.id, 834),
                _finding(issue_b.id, 831),
            )
        )
        session.add_all(
            (
                IssueHistoryRow(
                    id="weekly-regressed-in",
                    issue_id=issue_a.id,
                    kind="regressed",
                    actor_ref="member:triager",
                    at=NOW + timedelta(days=2),
                ),
                IssueHistoryRow(
                    id="weekly-escalated-in",
                    issue_id=issue_b.id,
                    kind="escalated",
                    actor_ref="system:worker",
                    at=NOW + timedelta(days=3),
                ),
                IssueHistoryRow(
                    id="weekly-regressed-out",
                    issue_id=issue_a.id,
                    kind="regressed",
                    actor_ref="member:triager",
                    at=NOW + timedelta(days=8),
                ),
            )
        )
    async with harness.sessions() as session:
        summary: WeeklySummary = await IssueReportSourceImpl().weekly_summary(
            session, NOW, NOW + timedelta(days=7)
        )
        assigned: int = await IssueReportSourceImpl().assigned_open_count(session, "member-x")
    assert summary.new_count == 3
    assert summary.resolved_count == 1
    assert summary.regressed_count == 1
    assert summary.escalating_count == 1
    assert summary.mttr_seconds == 172800
    assert tuple((issue.issue_id, issue.findings) for issue in summary.top_issues) == (
        (issue_a.id, 3),
        (issue_b.id, 1),
    )
    assert assigned == 1
    await harness.close()
