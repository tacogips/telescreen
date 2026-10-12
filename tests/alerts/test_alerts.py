"""Regression tests for alert validation, evaluation, and retention."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.alerts.evaluator import AlertEvaluator
from telescreen.alerts.maintenance import AlertMaintenanceProvider
from telescreen.alerts.model import (
    AlertActions,
    AlertFilters,
    AlertRule,
    AlertTrigger,
    actions_json,
    filters_json,
)
from telescreen.alerts.repository import AlertRepository
from telescreen.alerts.service import AlertRuleService
from telescreen.alerts.validate import RuleIssue, validate_rule
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json
from telescreen.core.vocabulary import (
    AlertFireOutcome,
    AlertTriggerKind,
    DlpVerdict,
    ErrorCode,
    IssueEventKind,
    IssuePriority,
    IssueStatus,
    NotificationChannel,
    RuleClass,
    RunTrigger,
    Severity,
    Visibility,
)
from telescreen.db.models import AlertFireRow, AlertRuleRow, FindingRow, IssueRow
from telescreen.masking.redact import Redactor
from telescreen.ports.issues import (
    FindingsRecorded,
    IssueEvent,
    IssueSnapshot,
    OpenResourceRef,
)
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.workflow.dsl.model import NotificationTargetSpec
from tests.notifications.v13.fakes import RecordingAudit
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, github_token


@dataclass
class FakeStarter:
    """Fake active-workflow lookup and start adapter."""

    events: tuple[IssueEventKind, ...] | None = ()
    raise_on_start: bool = False
    started: list[tuple[str, str, RunTrigger]] = field(default_factory=list)

    async def start_for_issue(
        self, session: AsyncSession, workflow_name: str, issue_id: str, trigger: RunTrigger
    ) -> str | None:
        """Record workflow starts or raise a configured safe error."""
        del session
        if self.raise_on_start:
            raise TelescreenError(ErrorCode.DELIVERY_FAILED, "workflow unavailable")
        self.started.append((workflow_name, issue_id, trigger))
        return "run-1"

    async def trigger_events(
        self, session: AsyncSession, workflow_name: str
    ) -> tuple[IssueEventKind, ...] | None:
        """Return configured event triggers."""
        del session, workflow_name
        return self.events


@dataclass
class FakeSender:
    """Capture safe outbound requests and return a configured DLP receipt."""

    verdict: DlpVerdict = DlpVerdict.PASS
    requests: list[NotificationRequest] = field(default_factory=list)

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Capture one request and report it queued unless DLP blocks it."""
        del session
        self.requests.append(request)
        blocked: bool = self.verdict is DlpVerdict.BLOCKED
        return NotificationReceipt(
            ("notification-1",) if not blocked else (), self.verdict, not blocked
        )


@dataclass(frozen=True)
class FakeIssueReader:
    """Read one masked issue snapshot for evaluator tests."""

    snapshot: IssueSnapshot

    async def get(self, session: AsyncSession, issue_id: str) -> IssueSnapshot | None:
        """Return the configured snapshot only for its id."""
        del session
        return self.snapshot if issue_id == self.snapshot.id else None

    async def open_resource_refs(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[OpenResourceRef, ...]:
        """Implement the unused issue reader operation."""
        del session, collector_config_id
        return ()


@pytest.mark.parametrize(
    ("trigger", "expected"),
    (
        (
            AlertTrigger(AlertTriggerKind.FREQUENCY),
            ("/trigger/threshold", "/trigger/window_minutes"),
        ),
        (AlertTrigger(AlertTriggerKind.ISSUE_CREATED), ()),
    ),
)
async def test_validation_returns_field_paths_and_codes_only(
    trigger: AlertTrigger, expected: tuple[str, ...]
) -> None:
    """Validation reports structural paths without echoing any submitted values."""
    harness: SqliteHarness = await make_sqlite_harness()
    rule: AlertRule = AlertRule(
        "safe-rule",
        trigger,
        AlertFilters(),
        AlertActions(
            (
                NotificationTargetSpec(
                    ("email:alerts@example.invalid",), (NotificationChannel.EMAIL,)
                ),
            )
        ),
    )
    async with harness.sessions() as session:
        issues: tuple[RuleIssue, ...] = await validate_rule(
            rule,
            Redactor.default(),
            FakeClock(datetime(2026, 10, 12, tzinfo=UTC)),
            None,
            None,
            session,
        )
    assert tuple(issue.field for issue in issues) == expected
    assert all(issue.code and issue.field.startswith("/") for issue in issues)
    await harness.close()


async def test_rule_name_with_sample_secret_returns_validation_error_without_row() -> None:
    """A detected rule name is rejected before any alert rule persistence."""
    harness: SqliteHarness = await make_sqlite_harness()
    sample: Sample = github_token()
    service: AlertRuleService = AlertRuleService(
        Redactor.default(),
        RecordingAudit(),
        FakeClock(datetime(2026, 10, 12, tzinfo=UTC)),
        None,
        None,
        AlertRepository(),
    )
    rule: AlertRule = AlertRule(
        sample.value,
        AlertTrigger(AlertTriggerKind.ISSUE_CREATED),
        AlertFilters(),
        AlertActions(
            (
                NotificationTargetSpec(
                    ("email:alerts@example.invalid",), (NotificationChannel.EMAIL,)
                ),
            )
        ),
    )
    async with harness.sessions() as session:
        issues: tuple[RuleIssue, ...] = await service.validate(session, rule)
    assert tuple((issue.field, issue.code) for issue in issues) == (
        ("/name", "raw_value_detected"),
    )
    with pytest.raises(TelescreenError) as caught:
        async with harness.sessions.begin() as session:
            await service.create(session, rule, "member:admin")
    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    async with harness.sessions() as session:
        rows: tuple[AlertRuleRow, ...] = tuple((await session.scalars(select(AlertRuleRow))).all())
    assert rows == ()
    await harness.close()


async def test_duplicate_trigger_is_rejected_for_same_issue_event() -> None:
    """D19 blocks an alert workflow action already triggered by the same event."""
    harness: SqliteHarness = await make_sqlite_harness()
    starter: FakeStarter = FakeStarter((IssueEventKind.ISSUE_CREATED,))
    rule: AlertRule = AlertRule(
        "rule-one",
        AlertTrigger(AlertTriggerKind.ISSUE_CREATED),
        AlertFilters(),
        AlertActions(start_workflow="incident-response"),
    )
    async with harness.sessions() as session:
        issues: tuple[RuleIssue, ...] = await validate_rule(
            rule,
            Redactor.default(),
            FakeClock(datetime(2026, 10, 12, tzinfo=UTC)),
            None,
            starter,
            session,
        )
    assert tuple((issue.field, issue.code) for issue in issues) == (
        ("/actions/start_workflow", "duplicate_trigger"),
    )
    await harness.close()


async def test_d21_only_one_interval_suppression_row_is_written() -> None:
    """Repeated matches inside one action window add at most one suppression row."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    snapshot: IssueSnapshot = _snapshot("issue-1", now)
    sender: FakeSender = FakeSender()
    evaluator: AlertEvaluator = AlertEvaluator(
        sender, FakeStarter(), FakeIssueReader(snapshot), clock, AlertRepository()
    )
    async with harness.sessions.begin() as session:
        session.add(_issue("issue-1", IssueStatus.UNRESOLVED.value, now))
        session.add(_rule("rule-1", AlertTriggerKind.ISSUE_CREATED, now))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", now))
    clock.advance(timedelta(minutes=1))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", clock.now()))
    clock.advance(timedelta(minutes=1))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", clock.now()))
    async with harness.sessions() as session:
        rows: tuple[AlertFireRow, ...] = tuple(
            (await session.scalars(select(AlertFireRow).order_by(AlertFireRow.fired_at))).all()
        )
    assert tuple(row.outcome for row in rows) == (
        AlertFireOutcome.DELIVERED.value,
        AlertFireOutcome.SUPPRESSED_INTERVAL.value,
    )
    assert len(sender.requests) == 1
    clock.advance(timedelta(minutes=60))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", clock.now()))
    async with harness.sessions() as session:
        rows = tuple(
            (await session.scalars(select(AlertFireRow).order_by(AlertFireRow.fired_at))).all()
        )
    assert tuple(row.outcome for row in rows)[-1] == AlertFireOutcome.DELIVERED.value
    await harness.close()


async def test_d21_interval_is_anchored_on_latest_delivery() -> None:
    """Suppressed matches do not extend the interval after a delivered fire."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    snapshot: IssueSnapshot = _snapshot("issue-1", now)
    sender: FakeSender = FakeSender()
    evaluator: AlertEvaluator = AlertEvaluator(
        sender, FakeStarter(), FakeIssueReader(snapshot), clock, AlertRepository()
    )
    async with harness.sessions.begin() as session:
        session.add(_issue("issue-1", IssueStatus.UNRESOLVED.value, now))
        session.add(_rule("rule-1", AlertTriggerKind.ISSUE_CREATED, now))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", now))
    clock.advance(timedelta(minutes=50))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", clock.now()))
    clock.advance(timedelta(minutes=11))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", clock.now()))
    clock.advance(timedelta(minutes=1))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", clock.now()))
    clock.advance(timedelta(minutes=8))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", clock.now()))
    async with harness.sessions() as session:
        rows: tuple[AlertFireRow, ...] = tuple(
            (await session.scalars(select(AlertFireRow).order_by(AlertFireRow.fired_at))).all()
        )
    assert tuple(row.outcome for row in rows) == (
        AlertFireOutcome.DELIVERED.value,
        AlertFireOutcome.SUPPRESSED_INTERVAL.value,
        AlertFireOutcome.DELIVERED.value,
        AlertFireOutcome.SUPPRESSED_INTERVAL.value,
    )
    assert len(sender.requests) == 2
    await harness.close()


async def test_d21_muted_rule_writes_one_muted_row_per_window_and_sends_nothing() -> None:
    """Mute fires are limited per window and never start the delivered interval."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    snapshot: IssueSnapshot = _snapshot("issue-1", now)
    sender: FakeSender = FakeSender()
    evaluator: AlertEvaluator = AlertEvaluator(
        sender, FakeStarter(), FakeIssueReader(snapshot), clock, AlertRepository()
    )
    rule: AlertRuleRow = _rule("rule-1", AlertTriggerKind.ISSUE_CREATED, now)
    rule.muted_until = now + timedelta(days=1)
    async with harness.sessions.begin() as session:
        session.add(_issue("issue-1", IssueStatus.UNRESOLVED.value, now))
        session.add(rule)
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", now))
        rows: tuple[AlertFireRow, ...] = tuple(
            (await session.scalars(select(AlertFireRow).order_by(AlertFireRow.fired_at))).all()
        )
        assert tuple(row.outcome for row in rows) == (AlertFireOutcome.MUTED.value,)
        assert len(sender.requests) == 0
    clock.advance(timedelta(minutes=1))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", clock.now()))
        rows = tuple(
            (await session.scalars(select(AlertFireRow).order_by(AlertFireRow.fired_at))).all()
        )
        assert tuple(row.outcome for row in rows) == (AlertFireOutcome.MUTED.value,)
        assert len(sender.requests) == 0
    clock.advance(timedelta(minutes=60))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", clock.now()))
        rows = tuple(
            (await session.scalars(select(AlertFireRow).order_by(AlertFireRow.fired_at))).all()
        )
        assert tuple(row.outcome for row in rows) == (
            AlertFireOutcome.MUTED.value,
            AlertFireOutcome.MUTED.value,
        )
        assert len(sender.requests) == 0
    async with harness.sessions.begin() as session:
        stored_rule: AlertRuleRow | None = await session.get(AlertRuleRow, "rule-1")
        assert stored_rule is not None
        stored_rule.muted_until = clock.now() - timedelta(minutes=1)
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", clock.now()))
        rows = tuple(
            (await session.scalars(select(AlertFireRow).order_by(AlertFireRow.fired_at))).all()
        )
    assert tuple(row.outcome for row in rows) == (
        AlertFireOutcome.MUTED.value,
        AlertFireOutcome.MUTED.value,
        AlertFireOutcome.DELIVERED.value,
    )
    assert len(sender.requests) == 1
    await harness.close()


async def test_archived_and_merged_child_frequency_rows_are_excluded() -> None:
    """Archived issues do not match frequency rules and child events resolve to primary."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    sender: FakeSender = FakeSender()
    async with harness.sessions.begin() as session:
        archived: IssueRow = _issue("archived", IssueStatus.ARCHIVED.value, now)
        session.add(archived)
        session.add(_finding("f-archived", archived.id, now))
        session.add(_rule("freq-archived", AlertTriggerKind.FREQUENCY, now, threshold=1))
    evaluator: AlertEvaluator = AlertEvaluator(
        sender, FakeStarter(), FakeIssueReader(_snapshot("archived", now)), clock, AlertRepository()
    )
    async with harness.sessions.begin() as session:
        await evaluator.on_findings_recorded(session, FindingsRecorded("archived", 1, now))
    async with harness.sessions.begin() as session:
        archived_rule: AlertRuleRow | None = await session.get(AlertRuleRow, "freq-archived")
        assert archived_rule is not None
        archived_rule.enabled = False
        primary: IssueRow = _issue("primary", IssueStatus.UNRESOLVED.value, now)
        child: IssueRow = _issue("child", IssueStatus.UNRESOLVED.value, now)
        child.merged_into_id = primary.id
        session.add_all(
            (
                primary,
                child,
                _finding("f-child", child.id, now),
                _rule("freq-primary", AlertTriggerKind.FREQUENCY, now, threshold=1),
            )
        )
    evaluator = AlertEvaluator(
        sender, FakeStarter(), FakeIssueReader(_snapshot("primary", now)), clock, AlertRepository()
    )
    async with harness.sessions.begin() as session:
        await evaluator.on_findings_recorded(session, FindingsRecorded("child", 1, now))
    async with harness.sessions() as session:
        rows: tuple[AlertFireRow, ...] = tuple((await session.scalars(select(AlertFireRow))).all())
    assert len(rows) == 1
    assert rows[0].issue_id == "primary"
    await harness.close()


async def test_frequency_five_in_ten_minutes_fires_only_at_threshold() -> None:
    """Frequency rules wait for the configured count and include the threshold finding."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    sender: FakeSender = FakeSender()
    evaluator: AlertEvaluator = AlertEvaluator(
        sender,
        FakeStarter(),
        FakeIssueReader(_snapshot("issue-frequency", now)),
        clock,
        AlertRepository(),
    )
    async with harness.sessions.begin() as session:
        session.add(_issue("issue-frequency", IssueStatus.UNRESOLVED.value, now))
        session.add(_rule("frequency-five", AlertTriggerKind.FREQUENCY, now, threshold=5))
        session.add_all(
            _finding(f"frequency-{index}", "issue-frequency", now) for index in range(4)
        )
    async with harness.sessions.begin() as session:
        await evaluator.on_findings_recorded(session, FindingsRecorded("issue-frequency", 4, now))
    async with harness.sessions() as session:
        fires_before_threshold: tuple[AlertFireRow, ...] = tuple(
            (await session.scalars(select(AlertFireRow))).all()
        )
    assert fires_before_threshold == ()
    async with harness.sessions.begin() as session:
        session.add(_finding("frequency-4", "issue-frequency", now))
    async with harness.sessions.begin() as session:
        await evaluator.on_findings_recorded(session, FindingsRecorded("issue-frequency", 1, now))
    async with harness.sessions() as session:
        fires_at_threshold: tuple[AlertFireRow, ...] = tuple(
            (await session.scalars(select(AlertFireRow))).all()
        )
    assert len(fires_at_threshold) == 1
    assert fires_at_threshold[0].outcome == AlertFireOutcome.DELIVERED.value
    await harness.close()


@pytest.mark.parametrize("failure", ("blocked", "failed"))
async def test_blocked_and_failed_outcomes_are_recorded(failure: str) -> None:
    """DLP blocks and workflow failures produce distinct safe fire outcomes."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    starter: FakeStarter = FakeStarter(raise_on_start=failure == "failed")
    if failure == "blocked":
        sender: FakeSender = FakeSender(DlpVerdict.BLOCKED)
        action: AlertActions = AlertActions(
            (
                NotificationTargetSpec(
                    ("email:alerts@example.invalid",), (NotificationChannel.EMAIL,)
                ),
            )
        )
        kind: AlertTriggerKind = AlertTriggerKind.ISSUE_CREATED
    else:
        sender = FakeSender()
        action = AlertActions(start_workflow="workflow")
        kind = AlertTriggerKind.ISSUE_CREATED
    evaluator: AlertEvaluator = AlertEvaluator(
        sender, starter, FakeIssueReader(_snapshot("issue-1", now)), clock, AlertRepository()
    )
    async with harness.sessions.begin() as session:
        session.add(_issue("issue-1", IssueStatus.UNRESOLVED.value, now))
        session.add(_rule("rule-outcome", kind, now, actions=action))
    async with harness.sessions.begin() as session:
        await evaluator.on_issue_event(session, _event("issue-1", now))
    async with harness.sessions() as session:
        row: AlertFireRow | None = await session.scalar(select(AlertFireRow))
    assert row is not None
    assert row.outcome == (
        AlertFireOutcome.BLOCKED.value if failure == "blocked" else AlertFireOutcome.FAILED.value
    )
    assert row.error_code == (None if failure == "blocked" else ErrorCode.DELIVERY_FAILED.value)
    await harness.close()


async def test_maintenance_keeps_89_day_fire_and_deletes_91_day_fire() -> None:
    """Alert fire retention is 90 days, measured against the injected clock."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    maintenance: AlertMaintenanceProvider = AlertMaintenanceProvider(
        AlertRepository(), FakeClock(now)
    )
    async with harness.sessions.begin() as session:
        session.add_all(
            (_fire("old", now - timedelta(days=91)), _fire("recent", now - timedelta(days=89)))
        )
    async with harness.sessions.begin() as session:
        deleted: int = await maintenance.tick(session, now)
    async with harness.sessions() as session:
        rows: tuple[AlertFireRow, ...] = tuple((await session.scalars(select(AlertFireRow))).all())
    assert deleted == 1
    assert tuple(row.id for row in rows) == ("recent",)
    async with harness.sessions.begin() as session:
        assert await maintenance.tick(session, now + timedelta(hours=1)) == 0
    await harness.close()


async def test_history_cursor_pages_same_timestamp_fires_without_skips() -> None:
    """Timestamp/id keyset pagination includes every fire with a shared timestamp."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    service: AlertRuleService = AlertRuleService(
        Redactor.default(), RecordingAudit(), clock, None, None, AlertRepository()
    )
    async with harness.sessions.begin() as session:
        session.add(_rule("history-rule", AlertTriggerKind.ISSUE_CREATED, now))
        session.add_all(_fire(f"fire-{index:02d}", now, "history-rule") for index in range(51))
    async with harness.sessions() as session:
        first_page: tuple[AlertFireRow, ...] = await service.history(session, "history-rule", None)
    assert len(first_page) == 51
    first_fifty: tuple[AlertFireRow, ...] = first_page[:50]
    cursor: str = f"{now.isoformat()}|{first_fifty[-1].id}"
    async with harness.sessions() as session:
        second_page: tuple[AlertFireRow, ...] = await service.history(
            session, "history-rule", cursor
        )
    assert tuple(row.id for row in second_page) == ("fire-00",)
    await harness.close()


def _snapshot(issue_id: str, now: datetime) -> IssueSnapshot:
    """Create one masked issue snapshot."""
    return IssueSnapshot(
        issue_id,
        1,
        "T-1",
        "Safe issue",
        IssueStatus.UNRESOLVED,
        "custom:test",
        RuleClass.TOKEN,
        Severity.HIGH,
        "masked location",
        1,
        now,
        now,
        None,
        None,
        priority=IssuePriority.HIGH,
        visibility=Visibility.UNKNOWN,
    )


def _issue(issue_id: str, status: str, now: datetime) -> IssueRow:
    """Create a minimally valid issue row."""
    return IssueRow(
        id=issue_id,
        number=sum(ord(character) for character in issue_id),
        grouping_key=f"group-{issue_id}",
        source="custom:test",
        resource_key="resource-key",
        display_locator="masked location",
        display_title="Safe issue",
        rule_class=RuleClass.TOKEN.value,
        severity=Severity.HIGH.value,
        status=status,
        first_seen=now,
        last_seen=now,
        last_checked_at=now,
        created_at=now,
        updated_at=now,
    )


def _finding(finding_id: str, issue_id: str, now: datetime) -> FindingRow:
    """Create one masked finding with a keyed fingerprint."""
    return FindingRow(
        id=finding_id,
        issue_id=issue_id,
        scan_job_id=None,
        source="custom:test",
        resource_key="resource-key",
        display_locator="masked location",
        location="segment:1",
        rule_id="token",
        rule_class=RuleClass.TOKEN.value,
        detector="rule",
        confidence=1.0,
        masked_snippet="[REDACTED]",
        fingerprint=f"fingerprint-{finding_id}",
        fingerprint_key_id="test-key",
        observed_at=now,
    )


def _rule(
    rule_id: str,
    trigger: AlertTriggerKind,
    now: datetime,
    threshold: int | None = None,
    actions: AlertActions | None = None,
) -> AlertRuleRow:
    """Create a stored enabled rule with safe empty filters and action JSON."""
    selected_actions: AlertActions = actions or AlertActions(
        (NotificationTargetSpec(("email:alerts@example.invalid",), (NotificationChannel.EMAIL,)),)
    )
    return AlertRuleRow(
        id=rule_id,
        name=rule_id,
        enabled=True,
        trigger_kind=trigger.value,
        threshold=threshold,
        window_minutes=10 if threshold else None,
        filters_json=dump_json(filters_json(AlertFilters())),
        actions_json=dump_json(actions_json(selected_actions)),
        action_interval_minutes=60,
        muted_until=None,
        last_fired_at=None,
        created_by="member:admin",
        created_at=now,
        updated_at=now,
    )


def _event(issue_id: str, now: datetime) -> IssueEvent:
    """Create one safe lifecycle event."""
    return IssueEvent(
        IssueEventKind.ISSUE_CREATED,
        "event-1",
        issue_id,
        "custom:test",
        RuleClass.TOKEN,
        Severity.HIGH,
        now,
    )


def _fire(fire_id: str, fired_at: datetime, rule_id: str = "rule") -> AlertFireRow:
    """Create a history row for retention testing."""
    return AlertFireRow(
        id=fire_id,
        rule_id=rule_id,
        issue_id="issue",
        trigger_kind="issue_created",
        outcome="delivered",
        notification_count=0,
        run_id=None,
        error_code=None,
        fired_at=fired_at,
    )
