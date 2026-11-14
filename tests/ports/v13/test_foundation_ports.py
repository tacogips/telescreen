"""Contract tests for v1.3 issue, notification, directory, and workflow ports."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import (
    ActorDisplayKind,
    ErrorCode,
    IssueHistoryKind,
    IssuePriority,
    IssueStatus,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
    RuleClass,
    RunTrigger,
    Severity,
    SuggestionReason,
)
from telescreen.ports.directory import ActorDisplay
from telescreen.ports.issues import (
    FindingsRecorded,
    IssueActivity,
    IssueSnapshot,
    NoopFindingsObserver,
    NoopIssueActivityNotifier,
    ReportIssue,
    SuggestedAssignee,
    WeeklySummary,
)
from telescreen.ports.notifications import (
    GuardVerdict,
    MemberNotification,
    NotificationContext,
    NotificationRequest,
    NotificationTester,
)
from telescreen.ports.notifications import (
    TestOutcome as NotificationTestOutcome,
)
from telescreen.ports.workflow import RunStarter

NOW: datetime = datetime(2026, 10, 12, tzinfo=UTC)


def test_v13_issue_dtos_construct_with_required_fields_only() -> None:
    """New issue DTOs have the expected required fields and defaulted extensions."""
    recorded: FindingsRecorded = FindingsRecorded("issue-1", 2, NOW)
    activity: IssueActivity = IssueActivity(
        IssueHistoryKind.COMMENTED, "issue-1", "system:test", NOW
    )
    report_issue: ReportIssue = ReportIssue("issue-1", "TELE-1", "Masked title", 3)
    summary: WeeklySummary = WeeklySummary(NOW, NOW, 1, 2, 3, 4, 5, None, (report_issue,))
    suggested: SuggestedAssignee = SuggestedAssignee("team:example", SuggestionReason.OWNER)
    snapshot: IssueSnapshot = IssueSnapshot(
        "issue-1",
        1,
        "TELE-1",
        "Masked title",
        IssueStatus.UNRESOLVED,
        "slack",
        RuleClass.CREDENTIAL,
        Severity.HIGH,
        "masked locator",
        1,
        NOW,
        NOW,
        None,
        None,
    )
    assert recorded.inserted == 2
    assert activity.comment_id is None
    assert summary.mttr_seconds is None
    assert suggested.reason is SuggestionReason.OWNER
    assert snapshot.priority is None
    assert snapshot.assignee_team_slug is None


def test_v13_notification_and_directory_dtos_construct() -> None:
    """New notification and actor DTOs retain only typed, safe fields."""
    member: MemberNotification = MemberNotification(
        "member-1",
        NotificationCategory.MENTIONS,
        NotificationEvent.ISSUE_MENTIONED,
        NotificationContext(comment_excerpt="Masked comment"),
    )
    request: NotificationRequest = NotificationRequest(
        NotificationChannel.EMAIL,
        ("member@example.invalid",),
        "subject",
        "body",
        "system:test",
        category=NotificationCategory.ALERTS,
    )
    outcome: NotificationTestOutcome = NotificationTestOutcome(True, None, "notification-1")
    guard: GuardVerdict = GuardVerdict(True, 0)
    display: ActorDisplay = ActorDisplay(
        "member:1", ActorDisplayKind.MEMBER, "Example", "example", True
    )
    assert member.issue_id is None
    assert request.category is NotificationCategory.ALERTS
    assert outcome.error_code is None
    assert guard.hit_count == 0
    assert display.active is True


@pytest.mark.asyncio
async def test_noop_observers_return_none() -> None:
    """The default observers accept their events without an external side effect."""
    session: AsyncSession = AsyncSession()
    try:
        observed: None = await NoopFindingsObserver().on_findings_recorded(
            session, FindingsRecorded("issue-1", 1, NOW)
        )
        notified: None = await NoopIssueActivityNotifier().on_activity(
            session, IssueActivity(IssueHistoryKind.COMMENTED, "issue-1", "system:test", NOW)
        )
    finally:
        await session.close()
    assert observed is None
    assert notified is None


def test_v13_protocol_supporting_types_are_typed() -> None:
    """Port DTO imports expose typed values without expanding existing protocols."""
    assert IssuePriority.HIGH.value == "high"
    assert ErrorCode.INVALID_QUERY.value == "invalid_query"
    assert RunTrigger.ALERT_RULE.value == "alert_rule"
    assert NotificationTester.__name__ == "NotificationTester"
    assert RunStarter.__name__ == "RunStarter"
