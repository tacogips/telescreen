"""Behavioral tests for remediation, exception and digest operations."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_object
from telescreen.core.vocabulary import (
    CoverageState,
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
    ExceptionDuration,
    ExceptionStatus,
    IssueHistoryKind,
    IssueResolution,
    IssueStatus,
    NotificationChannel,
    PolicyEffect,
    RemediationAction,
    RuleClass,
    Severity,
)
from telescreen.db.models import (
    FindingRow,
    IssueExceptionRow,
    IssueHistoryRow,
    IssueRow,
    MemberRow,
)
from telescreen.issues.digest import OwnerDigestHandler
from telescreen.issues.exceptions import ExceptionService
from telescreen.issues.lifecycle import IssueRemediationService
from telescreen.issues.maintenance import ExceptionMaintenanceProvider
from telescreen.issues.occurrences import OccurrenceResult, linked
from telescreen.issues.verification import VerifyRescanHandler
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import DetectorKind, RescanKind, RescanResult
from telescreen.ports.directory import AssigneeIds, OwnerDirectory, ResolvedRecipients
from telescreen.ports.issues import RemediationReceipt
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.ports.policy import PolicyDecision, PolicySubject
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind, TaskSpec
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class RecordingAudit:
    """Capture audit calls without writing a duplicate audit chain."""

    entries: list[AuditEntry]

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record a safe audit value."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class RecordingQueue:
    """Capture queue specs and enforce idempotency in memory."""

    specs: list[TaskSpec]
    keys: set[str]

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Record each idempotency key once."""
        del session
        if spec.idempotency_key in self.keys:
            return False
        self.keys.add(spec.idempotency_key)
        self.specs.append(spec)
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Satisfy the queue port for these tests."""
        del session, ref_id
        return 0


@dataclass
class FakeOwners(OwnerDirectory):
    """Resolve test members and teams through stable references."""

    async def member_ref(self, session: AsyncSession, member_id: str) -> str | None:
        """Return a member ref for known test members."""
        del session
        return f"member:{member_id}" if member_id else None

    async def team_ref(self, session: AsyncSession, team_id: str) -> str | None:
        """Return a team slug for the configured team id."""
        del session
        return "ops" if team_id == "team-id" else None

    async def team_refs_of(self, session: AsyncSession, member_id: str) -> tuple[str, ...]:
        """Return no additional teams for test members."""
        del session, member_id
        return ()

    async def member_for_owner(self, session: AsyncSession, owner_ref: str) -> str | None:
        """Return no identity mapping by default."""
        del session, owner_ref
        return None

    async def team_lead(self, session: AsyncSession, team_slug: str) -> str | None:
        """Return the configured lead for the test operations team."""
        del session
        return "lead-1" if team_slug == "ops" else None


@dataclass
class FakeResolver:
    """Resolve safe refs to one Slack or email test recipient."""

    slack: bool = True
    assigned_team: bool = True

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Return stable test recipient addresses."""
        del session, ref
        return ResolvedRecipients(
            ("member@example.test",), (), ("slack-user",) if self.slack else (), ("alice",)
        )

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Accept only the lead identity for the lead ref."""
        del session
        return member_id == "lead-1" and ref == "member:lead-1"

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Resolve a test team ref to a team id."""
        del session
        return (
            AssigneeIds(None, "team-id")
            if self.assigned_team and ref == "team:ops"
            else AssigneeIds(None, None)
        )


@dataclass
class FakePolicy:
    """Return one stable owner-team policy decision."""

    owner_team: str | None = "ops"

    async def decide(
        self, session: AsyncSession, subject: PolicySubject, now: datetime
    ) -> PolicyDecision:
        """Return the configured test team without reading source data."""
        del session, subject, now
        return PolicyDecision(PolicyEffect.ALLOW, None, None, self.owner_team, True)


@dataclass
class RecordingNotifications:
    """Capture notification requests."""

    requests: list[NotificationRequest]

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Store only the test request value."""
        del session
        self.requests.append(request)
        return NotificationReceipt(("notification-id",), DlpVerdict.PASS, True)


@dataclass
class RecordingRescanner:
    """Return one configured safe rescan result."""

    result: RescanResult

    async def rescan(
        self, issue_id: str, detectors: tuple[DetectorKind, ...] | None = None
    ) -> RescanResult:
        """Ignore raw source access and return the safe result."""
        del issue_id, detectors
        return self.result


@dataclass
class RemediationHarness:
    """Own the SQLite database and p06 services for one test."""

    db: SqliteHarness
    clock: FakeClock
    audit: RecordingAudit
    queue: RecordingQueue
    owners: FakeOwners
    resolver: FakeResolver
    notifications: RecordingNotifications
    exceptions: ExceptionService
    service: IssueRemediationService

    async def close(self) -> None:
        """Dispose of the test database."""
        await self.db.close()


async def make_harness(slack: bool = True) -> RemediationHarness:
    """Create an isolated service fixture with local fakes."""
    db: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 10, 12, tzinfo=UTC))
    audit: RecordingAudit = RecordingAudit([])
    queue: RecordingQueue = RecordingQueue([], set())
    owners: FakeOwners = FakeOwners()
    resolver: FakeResolver = FakeResolver(slack)
    notifications: RecordingNotifications = RecordingNotifications([])
    exceptions: ExceptionService = ExceptionService(
        audit,
        clock,
        Redactor.default(),
        owners,
        resolver,
        FakePolicy(),
        notifications=notifications,
    )
    service: IssueRemediationService = IssueRemediationService(
        audit, clock, Redactor.default(), queue, resolver, owners, FakePolicy(), exceptions
    )
    return RemediationHarness(
        db, clock, audit, queue, owners, resolver, notifications, exceptions, service
    )


def make_issue(
    issue_id: str,
    number: int,
    status: IssueStatus = IssueStatus.UNRESOLVED,
    rule_class: RuleClass = RuleClass.TOKEN,
    has_resource: bool = True,
    assignee_member_id: str | None = None,
) -> IssueRow:
    """Build a safe issue row with the database's required base fields."""
    now: datetime = datetime(2026, 10, 10, 12, tzinfo=UTC)
    return IssueRow(
        id=issue_id,
        number=number,
        grouping_key=f"group-{issue_id}",
        source="github",
        resource_key=f"resource-{issue_id}",
        display_locator="masked-resource",
        resource_ref_json='{"id":"opaque"}' if has_resource else None,
        collector_config_id="collector-1" if has_resource else None,
        display_title="safe title fixture",
        rule_class=rule_class.value,
        severity=Severity.HIGH.value,
        status=status.value,
        assignee_member_id=assignee_member_id,
        assignee_team_id=None,
        first_seen=now,
        last_seen=now,
        last_checked_at=now,
        sweep_covered=False,
        times_seen=1,
        distinct_fingerprints=1,
        regression_count=0,
        resolved_at=None,
        resolution=None,
        created_at=now,
        updated_at=now,
    )


def make_finding(issue_id: str, fingerprint: str, version: int = 1) -> FindingRow:
    """Build a finding fixture with only safe masked metadata."""
    return FindingRow(
        id=f"finding-{issue_id}-{fingerprint}-{version}",
        issue_id=issue_id,
        scan_job_id="scan-1",
        source="github",
        resource_key=f"resource-{issue_id}",
        display_locator="masked-resource",
        location="segment:1",
        rule_id="github_token",
        rule_class=RuleClass.TOKEN.value,
        detector="rule",
        confidence=0.9,
        masked_snippet="[REDACTED]",
        fingerprint=fingerprint,
        fingerprint_key_id=f"key-{version}",
        fingerprint_key_version=version,
        observed_at=datetime(2026, 10, 10, 12, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_request_verification_enqueues_per_history_and_rejects_resolved() -> None:
    """Queue a safe rescan and reject an invalid source status."""
    harness: RemediationHarness = await make_harness()
    try:
        async with harness.db.sessions.begin() as session:
            session.add(make_issue("issue-1", 1))
        async with harness.db.sessions.begin() as session:
            receipt: RemediationReceipt = await harness.service.request_verification(
                session, "issue-1", RemediationAction.REMOVE, False, "member:alice", "reviewed"
            )
        async with harness.db.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, "issue-1")
            history: IssueHistoryRow | None = await session.scalar(
                select(IssueHistoryRow).where(IssueHistoryRow.issue_id == "issue-1")
            )
        assert receipt.issue_ids == ("issue-1",)
        assert receipt.rescans_enqueued == 1
        assert issue is not None and issue.status == IssueStatus.PENDING_VERIFICATION.value
        assert history is not None and history.kind == IssueHistoryKind.VERIFICATION_REQUESTED.value
        assert harness.queue.specs[0].kind is EngineTaskKind.VERIFY_RESCAN
        async with harness.db.sessions.begin() as session:
            current: IssueRow | None = await session.get(IssueRow, "issue-1")
            assert current is not None
            current.status = IssueStatus.RESOLVED.value
        with pytest.raises(TelescreenError) as captured:
            async with harness.db.sessions.begin() as session:
                await harness.service.request_verification(
                    session, "issue-1", RemediationAction.REMOVE, False, "member:alice", None
                )
        assert captured.value.code is ErrorCode.INVALID_TRANSITION
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_verification_without_reference_still_marks_pending_without_task() -> None:
    """An issue lacking rescan reference remains pending without queue work."""
    harness: RemediationHarness = await make_harness()
    try:
        async with harness.db.sessions.begin() as session:
            session.add(make_issue("issue-1", 1, has_resource=False))
        async with harness.db.sessions.begin() as session:
            receipt: RemediationReceipt = await harness.service.request_verification(
                session, "issue-1", RemediationAction.RESTRICT, False, "member:alice", None
            )
        assert receipt.rescans_enqueued == 0
        assert harness.queue.specs == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_linked_remediation_is_direct_only_and_credentials_only() -> None:
    """Apply to direct credential occurrences and leave PII unlinked."""
    harness: RemediationHarness = await make_harness()
    try:
        async with harness.db.sessions.begin() as session:
            session.add_all(
                (
                    make_issue("root", 1),
                    make_issue("linked-1", 2),
                    make_issue("linked-2", 3),
                    make_issue("pii", 4, rule_class=RuleClass.EMAIL),
                    make_finding("root", "same"),
                    make_finding("linked-1", "same"),
                    make_finding("linked-2", "same"),
                )
            )
        async with harness.db.sessions.begin() as session:
            receipt: RemediationReceipt = await harness.service.request_verification(
                session, "root", RemediationAction.REMOVE, True, "member:alice", None
            )
        assert len(receipt.issue_ids) == 3
        assert receipt.rescans_enqueued == 3
        async with harness.db.sessions.begin() as session:
            receipt: RemediationReceipt = await harness.service.request_verification(
                session, "pii", RemediationAction.REMOVE, True, "member:alice", None
            )
        assert receipt.issue_ids == ("pii",)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_resolve_reason_and_exception_approval_revoke_and_expiry() -> None:
    """Enforce resolution reasons, approver rights, revoke, and expiry."""
    harness: RemediationHarness = await make_harness()
    try:
        async with harness.db.sessions.begin() as session:
            session.add_all((make_issue("issue-1", 1), make_issue("issue-2", 2)))
        with pytest.raises(TelescreenError) as captured:
            async with harness.db.sessions.begin() as session:
                await harness.service.resolve_with_reason(
                    session,
                    "issue-1",
                    IssueResolution.FALSE_POSITIVE,
                    " ",
                    "member:alice",
                    False,
                )
        assert captured.value.code is ErrorCode.VALIDATION_ERROR
        with pytest.raises(TelescreenError) as captured:
            async with harness.db.sessions.begin() as session:
                await harness.service.resolve_with_reason(
                    session,
                    "issue-1",
                    IssueResolution.MANUAL_UNVERIFIED,
                    "checked",
                    "member:alice",
                    False,
                )
        assert captured.value.code is ErrorCode.FORBIDDEN
        async with harness.db.sessions.begin() as session:
            await harness.service.resolve_with_reason(
                session,
                "issue-1",
                IssueResolution.MANUAL_UNVERIFIED,
                "checked",
                "member:admin",
                True,
            )
        async with harness.db.sessions.begin() as session:
            exception_id: str = await harness.exceptions.request(
                session,
                "issue-2",
                "business exception",
                ExceptionDuration.DAYS_30,
                "member:alice",
                False,
            )
        assert harness.notifications.requests[0].body.startswith("TELE-2")
        with pytest.raises(TelescreenError) as captured:
            async with harness.db.sessions.begin() as session:
                await harness.exceptions.approve(
                    session, exception_id, "member:alice", "alice", False
                )
        assert captured.value.code is ErrorCode.FORBIDDEN
        async with harness.db.sessions.begin() as session:
            await harness.exceptions.approve(
                session, exception_id, "member:lead-1", "lead-1", False
            )
        async with harness.db.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, "issue-2")
            row: IssueExceptionRow | None = await session.get(IssueExceptionRow, exception_id)
        assert issue is not None and issue.status == IssueStatus.IGNORED.value
        assert issue.resolution == IssueResolution.ACCEPTED_RISK.value
        assert row is not None and row.expires_at is not None
        assert ensure_utc(row.expires_at) == harness.clock.now() + timedelta(days=30)
        async with harness.db.sessions.begin() as session:
            await harness.exceptions.revoke(session, exception_id, "member:alice", "alice", False)
        async with harness.db.sessions() as session:
            issue = await session.get(IssueRow, "issue-2")
        assert issue is not None and issue.status == IssueStatus.UNRESOLVED.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_exception_conflicts_forever_gate_and_resolved_request_rejection() -> None:
    """Reject duplicate and unauthorized exceptions and close pending requests."""
    harness: RemediationHarness = await make_harness()
    try:
        async with harness.db.sessions.begin() as session:
            session.add_all(
                (make_issue("requested", 1), make_issue("forever", 2), make_issue("closed", 3))
            )
        async with harness.db.sessions.begin() as session:
            exception_id: str = await harness.exceptions.request(
                session,
                "requested",
                "business exception",
                ExceptionDuration.DAYS_7,
                "member:alice",
                False,
            )
        notification: NotificationRequest = harness.notifications.requests[0]
        assert notification.body.startswith("TELE-1")
        assert "business exception" not in notification.body
        assert "member@example.test" not in notification.body
        async with harness.db.sessions() as session:
            row: IssueExceptionRow | None = await session.get(IssueExceptionRow, exception_id)
        assert row is not None and row.status == ExceptionStatus.REQUESTED.value
        with pytest.raises(TelescreenError) as captured:
            async with harness.db.sessions.begin() as session:
                await harness.exceptions.request(
                    session,
                    "requested",
                    "duplicate",
                    ExceptionDuration.DAYS_7,
                    "member:alice",
                    False,
                )
        assert captured.value.code is ErrorCode.CONFLICT
        with pytest.raises(TelescreenError) as captured:
            async with harness.db.sessions.begin() as session:
                await harness.exceptions.request(
                    session,
                    "forever",
                    "business exception",
                    ExceptionDuration.FOREVER,
                    "member:alice",
                    False,
                )
        assert captured.value.code is ErrorCode.FORBIDDEN
        async with harness.db.sessions.begin() as session:
            closed_request_id: str = await harness.exceptions.request(
                session,
                "closed",
                "business exception",
                ExceptionDuration.DAYS_7,
                "member:alice",
                False,
            )
        async with harness.db.sessions.begin() as session:
            await harness.service.resolve_with_reason(
                session,
                "closed",
                IssueResolution.FALSE_POSITIVE,
                "confirmed false positive",
                "member:alice",
                False,
            )
        with pytest.raises(TelescreenError) as captured:
            async with harness.db.sessions.begin() as session:
                await harness.exceptions.approve(
                    session, closed_request_id, "member:lead-1", "lead-1", False
                )
        assert captured.value.code is ErrorCode.CONFLICT
        async with harness.db.sessions() as session:
            closed_row: IssueExceptionRow | None = await session.get(
                IssueExceptionRow, closed_request_id
            )
        assert closed_row is not None and closed_row.status == ExceptionStatus.REJECTED.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_release_to_team_clears_member_assignee_and_records_target() -> None:
    """Release an issue to the policy team and record its stable team slug."""
    harness: RemediationHarness = await make_harness()
    try:
        async with harness.db.sessions.begin() as session:
            session.add(make_issue("issue-1", 1, assignee_member_id="alice"))
        async with harness.db.sessions.begin() as session:
            await harness.service.release_to_team(session, "issue-1", "member:alice")
        async with harness.db.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, "issue-1")
            history: IssueHistoryRow | None = await session.scalar(
                select(IssueHistoryRow).where(
                    IssueHistoryRow.issue_id == "issue-1",
                    IssueHistoryRow.kind == IssueHistoryKind.ASSIGNED.value,
                )
            )
        assert issue is not None and issue.assignee_member_id is None
        assert issue.assignee_team_id == "team-id"
        assert history is not None and history.to_value == "team:ops"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_expire_due_reopens_only_ignored_issue_and_audits() -> None:
    """Expire due rows and reopen an ignored issue with history."""
    harness: RemediationHarness = await make_harness()
    try:
        now: datetime = harness.clock.now()
        async with harness.db.sessions.begin() as session:
            session.add(make_issue("issue-1", 1, IssueStatus.IGNORED, assignee_member_id="alice"))
            session.add(
                IssueExceptionRow(
                    id="exception-1",
                    issue_id="issue-1",
                    reason="safe reason",
                    duration=ExceptionDuration.DAYS_30.value,
                    status=ExceptionStatus.ACTIVE.value,
                    requested_by="member:alice",
                    approver_ref="member:lead-1",
                    decided_by="member:lead-1",
                    decided_at=now - timedelta(days=31),
                    expires_at=now - timedelta(seconds=1),
                    created_at=now - timedelta(days=31),
                )
            )
        async with harness.db.sessions.begin() as session:
            count: int = await harness.exceptions.expire_due(session, now)
        async with harness.db.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, "issue-1")
            history: IssueHistoryRow | None = await session.scalar(
                select(IssueHistoryRow).where(
                    IssueHistoryRow.issue_id == "issue-1",
                    IssueHistoryRow.kind == IssueHistoryKind.EXCEPTION_EXPIRED.value,
                )
            )
        assert count == 1
        assert issue is not None and issue.status == IssueStatus.UNRESOLVED.value
        assert history is not None
        assert harness.notifications.requests[0].body.startswith("TELE-1")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_occurrences_require_same_version_and_exclude_resolved() -> None:
    """Link exact keyed fingerprint versions and omit resolved issues."""
    harness: RemediationHarness = await make_harness()
    try:
        async with harness.db.sessions.begin() as session:
            session.add_all(
                (
                    make_issue("root", 1),
                    make_issue("linked", 2),
                    make_issue("rotated", 3),
                    make_issue("resolved", 4, IssueStatus.RESOLVED),
                    make_finding("root", "same", 1),
                    make_finding("linked", "same", 1),
                    make_finding("rotated", "same", 2),
                    make_finding("resolved", "same", 1),
                )
            )
        async with harness.db.sessions() as session:
            occurrences: OccurrenceResult = await linked(session, "root")
        assert tuple(item.issue_id for item in occurrences.items) == ("linked",)
        assert occurrences.key_versions == (1,)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_verification_inconclusive_notifies_and_clean_does_not_change() -> None:
    """Keep inconclusive issues pending and leave clean outcomes to record path."""
    harness: RemediationHarness = await make_harness()
    try:
        async with harness.db.sessions.begin() as session:
            session.add(
                make_issue(
                    "issue-1", 1, IssueStatus.PENDING_VERIFICATION, assignee_member_id="alice"
                )
            )
        inconclusive: VerifyRescanHandler = VerifyRescanHandler(
            harness.db.sessions,
            RecordingRescanner(
                RescanResult(RescanKind.INCONCLUSIVE, coverage=CoverageState.TOO_LARGE)
            ),
            harness.audit,
            harness.clock,
            harness.owners,
            harness.resolver,
            harness.notifications,
        )
        task: ClaimedTask = ClaimedTask(
            "task-1", EngineTaskKind.VERIFY_RESCAN, "issue-1", json_object(()), 1, 5, "verify:1"
        )
        result: TaskResult = await inconclusive.handle(task)
        async with harness.db.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, "issue-1")
            history: IssueHistoryRow | None = await session.scalar(
                select(IssueHistoryRow).where(
                    IssueHistoryRow.kind == IssueHistoryKind.VERIFICATION_INCONCLUSIVE.value
                )
            )
        assert result.kind is TaskResultKind.DONE
        assert issue is not None and issue.status == IssueStatus.PENDING_VERIFICATION.value
        assert history is not None and history.to_value == CoverageState.TOO_LARGE.value
        assert len(harness.notifications.requests) == 1
        clean: VerifyRescanHandler = VerifyRescanHandler(
            harness.db.sessions,
            RecordingRescanner(RescanResult(RescanKind.CLEAN)),
            harness.audit,
            harness.clock,
            harness.owners,
            harness.resolver,
            harness.notifications,
        )
        await clean.handle(task)
        assert len(harness.notifications.requests) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_digest_uses_slack_and_omits_title_locator_and_email() -> None:
    """Digest content contains public short ids and enum/risk fields only."""
    harness: RemediationHarness = await make_harness()
    try:
        now: datetime = harness.clock.now()
        async with harness.db.sessions.begin() as session:
            session.add(
                MemberRow(
                    id="alice",
                    handle="alice",
                    display_name="Alice",
                    email="member@example.test",
                    slack_user_id="slack-user",
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(make_issue("issue-1", 1, assignee_member_id="alice"))
        handler: OwnerDigestHandler = OwnerDigestHandler(
            harness.db.sessions,
            harness.owners,
            harness.resolver,
            harness.notifications,
            harness.clock,
        )
        task: ClaimedTask = ClaimedTask(
            "task-1", EngineTaskKind.OWNER_DIGEST, "owner-digest", json_object(()), 1, 5, "digest:1"
        )
        result: TaskResult = await handler.handle(task)
        request: NotificationRequest = harness.notifications.requests[0]
        assert result.kind is TaskResultKind.DONE
        assert request.channel is NotificationChannel.SLACK
        assert "TELE-1" in request.body
        assert "safe title fixture" not in request.body
        assert "masked-resource" not in request.body
        assert "member@example.test" not in request.body
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_maintenance_enqueues_only_recent_cron_fire_once() -> None:
    """Use recent cron fires only and preserve idempotency keys."""
    harness: RemediationHarness = await make_harness()
    try:
        queue: RecordingQueue = harness.queue
        old_provider: ExceptionMaintenanceProvider = ExceptionMaintenanceProvider(
            harness.exceptions, queue, "0 0 * * *"
        )
        async with harness.db.sessions.begin() as session:
            old_count: int = await old_provider.tick(session, harness.clock.now())
        assert old_count == 0
        provider: ExceptionMaintenanceProvider = ExceptionMaintenanceProvider(
            harness.exceptions, queue, "0 12 * * *"
        )
        harness.clock.advance(timedelta(minutes=5))
        async with harness.db.sessions.begin() as session:
            count: int = await provider.tick(session, harness.clock.now())
        async with harness.db.sessions.begin() as session:
            harness.clock.advance(timedelta(seconds=60))
            duplicate_count: int = await provider.tick(session, harness.clock.now())
        assert count == 1
        assert duplicate_count == 0
        assert len(queue.specs) == 1
        assert queue.specs[0].idempotency_key.startswith("digest:")
    finally:
        await harness.close()
