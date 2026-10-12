"""Archive metadata is cleared on every supported lifecycle exit."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.ids import new_id
from telescreen.core.json import json_null
from telescreen.core.vocabulary import (
    ArchiveKind,
    EngineTaskKind,
    ExceptionDuration,
    ExceptionStatus,
    IssueEventKind,
    IssueHistoryKind,
    IssueResolution,
    IssueStatus,
    PolicyEffect,
    RemediationAction,
    Visibility,
)
from telescreen.db.models import (
    FindingRow,
    IssueExceptionRow,
    IssueHistoryRow,
    IssueMergeRow,
    IssueRow,
)
from telescreen.directory import DirectoryRecipientResolver
from telescreen.directory.owners import DirectoryOwnerResolver
from telescreen.issues.archive import (
    ArchivePolicy,
    ArchiveRequest,
    RebaseScope,
    archive_issue,
    evaluate_after_ingest,
    rebase_archive,
)
from telescreen.issues.exceptions import ExceptionService
from telescreen.issues.groups import recompute_group_counters
from telescreen.issues.lifecycle import IssueRemediationService
from telescreen.issues.policy_apply import PolicyReevaluateHandler
from telescreen.issues.service import IssueService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.policy import PolicyDecision, PolicySubject
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskSpec
from tests.issues.v13_lifecycle.test_archive import (
    NOW,
    RecordingActivity,
    RecordingAudit,
    RecordingEvents,
    RecordingIndexer,
    StaticPolicyDecider,
    _finding,
    _issue,
    _world,
)
from tests.support.db import SqliteHarness


def _archive_columns(
    issue: IssueRow,
) -> tuple[str | None, datetime | None, int | None, int | None, datetime | None]:
    """Return all persisted archive columns for a lifecycle assertion."""
    return (
        issue.archive_kind,
        issue.archive_until,
        issue.archive_threshold,
        issue.archive_baseline,
        issue.archived_at,
    )


@dataclass
class EmptyQueue:
    """Skip rescan enqueue for issues without a resource reference."""

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Satisfy the queue port without external work."""
        del session, spec
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Satisfy the queue port without external work."""
        del session, ref_id
        return 0


@dataclass(frozen=True)
class AllowDecider:
    """Return an ALLOW decision to the policy reevaluation handler."""

    async def decide(
        self, session: AsyncSession, subject: PolicySubject, now: datetime
    ) -> PolicyDecision:
        """Return a stable allow decision."""
        del session, subject, now
        return PolicyDecision(PolicyEffect.ALLOW, "test-policy", 1, None, False)


def _services(
    audit: RecordingAudit,
    events: RecordingEvents,
    activity: RecordingActivity,
) -> tuple[IssueService, ExceptionService, IssueRemediationService]:
    """Construct local lifecycle services over the shared SQLite fixtures."""
    clock: FakeClock = FakeClock(NOW)
    owners: DirectoryOwnerResolver = DirectoryOwnerResolver()
    resolver: DirectoryRecipientResolver = DirectoryRecipientResolver()
    redactor: Redactor = Redactor.default()
    decider: StaticPolicyDecider = StaticPolicyDecider()
    fingerprinter: Fingerprinter = Fingerprinter(b"p" * 32, "test-key")
    service: IssueService = IssueService(
        audit,
        events,
        fingerprinter,
        redactor,
        resolver,
        clock,
        activity=activity,
        decider=decider,
    )
    exceptions: ExceptionService = ExceptionService(
        audit, clock, redactor, owners, resolver, decider
    )
    remediation: IssueRemediationService = IssueRemediationService(
        audit,
        clock,
        redactor,
        EmptyQueue(),
        resolver,
        owners,
        decider,
        exceptions,
    )
    return service, exceptions, remediation


async def _archive(
    session: AsyncSession,
    issue: IssueRow,
    audit: RecordingAudit,
    activity: RecordingActivity,
) -> None:
    """Archive a fixture issue under a bounded count condition."""
    await archive_issue(
        session,
        issue,
        ArchiveRequest(ArchiveKind.UNTIL_VALUES, count=5),
        "member:triager",
        NOW,
        ArchivePolicy(),
        audit,
        activity,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_request", (False, True))
async def test_exception_approval_paths_clear_archive(admin_request: bool) -> None:
    """Both direct admin exception and requested approval clear archive state."""
    db: SqliteHarness
    audit: RecordingAudit
    activity: RecordingActivity
    db, audit, _, activity = await _world()
    issue: IssueRow = _issue(f"exception-archive-{admin_request}", 30)
    service: ExceptionService = ExceptionService(
        audit,
        FakeClock(NOW),
        Redactor.default(),
        DirectoryOwnerResolver(),
        DirectoryRecipientResolver(),
        StaticPolicyDecider(),
    )
    async with db.sessions.begin() as session:
        session.add(issue)
        await session.flush()
        await _archive(session, issue, audit, activity)
        exception_id: str = await service.request(
            session,
            issue.id,
            "approved reason",
            ExceptionDuration.DAYS_30,
            "member:requester",
            admin_request,
        )
        if not admin_request:
            await service.approve(session, exception_id, "member:admin", None, True)
        exception: IssueExceptionRow | None = await session.get(IssueExceptionRow, exception_id)
        assert issue.status == IssueStatus.IGNORED.value
        assert _archive_columns(issue) == (None, None, None, None, None)
        assert issue.needs_review is False
        assert exception is not None and exception.status == ExceptionStatus.ACTIVE.value
        rows: tuple[IssueExceptionRow, ...] = tuple(
            (await session.scalars(select(IssueExceptionRow))).all()
        )
        assert len(rows) == 1
    await db.close()


@pytest.mark.asyncio
async def test_verification_request_clears_archive() -> None:
    """Requesting verification from archived clears all archive metadata."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("verification-archive", 31)
    service: IssueService
    remediation: IssueRemediationService
    service, _, remediation = _services(audit, events, activity)
    async with db.sessions.begin() as session:
        session.add(issue)
        await session.flush()
        await _archive(session, issue, audit, activity)
        await remediation.request_verification(
            session, issue.id, RemediationAction.REMOVE, False, "member:triager", None
        )
        assert issue.status == IssueStatus.PENDING_VERIFICATION.value
        assert _archive_columns(issue) == (None, None, None, None, None)
    await db.close()


@pytest.mark.asyncio
async def test_policy_allow_clears_archive_without_exception_row() -> None:
    """Policy ALLOW clears archive metadata without creating an exception."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("policy-archive", 32)
    service: IssueService
    service, _, _ = _services(audit, events, activity)
    service.decider = AllowDecider()
    async with db.sessions.begin() as session:
        session.add(issue)
        await session.flush()
        await _archive(session, issue, audit, activity)
    handler: PolicyReevaluateHandler = PolicyReevaluateHandler(
        db.sessions, service, RecordingIndexer([]), FakeClock(NOW)
    )
    task: ClaimedTask = ClaimedTask(
        "task-policy-archive", EngineTaskKind.POLICY_REEVALUATE, "policy", json_null(), 1, 5, "test"
    )
    result: TaskResult = await handler.handle(task)
    async with db.sessions() as session:
        stored: IssueRow | None = await session.get(IssueRow, issue.id)
        exceptions: tuple[IssueExceptionRow, ...] = tuple(
            (await session.scalars(select(IssueExceptionRow))).all()
        )
    assert result.kind.value == "done"
    assert stored is not None and stored.status == IssueStatus.ALLOWED.value
    assert _archive_columns(stored) == (None, None, None, None, None)
    assert exceptions == ()
    await db.close()


@pytest.mark.asyncio
async def test_reasoned_resolve_and_change_status_clear_archive() -> None:
    """Reasoned resolve and a supported status change clear archive columns."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    resolved: IssueRow = _issue("resolved-archive", 33)
    changed: IssueRow = _issue("changed-archive", 34)
    service: IssueService
    remediation: IssueRemediationService
    service, _, remediation = _services(audit, events, activity)
    async with db.sessions.begin() as session:
        session.add_all((resolved, changed))
        await session.flush()
        await _archive(session, resolved, audit, activity)
        await _archive(session, changed, audit, activity)
        await remediation.resolve_with_reason(
            session,
            resolved.id,
            IssueResolution.FALSE_POSITIVE,
            "reviewed safely",
            "member:triager",
            False,
        )
        await service.change_status(session, changed.id, IssueStatus.UNRESOLVED, "member:triager")
        assert resolved.status == IssueStatus.RESOLVED.value
        assert _archive_columns(resolved) == (None, None, None, None, None)
        assert changed.status == IssueStatus.UNRESOLVED.value
        assert _archive_columns(changed) == (None, None, None, None, None)
        history: tuple[IssueHistoryRow, ...] = tuple(
            (await session.scalars(select(IssueHistoryRow))).all()
        )
        assert any(
            row.issue_id == changed.id and row.kind == IssueHistoryKind.STATUS_CHANGED.value
            for row in history
        )
    await db.close()


@pytest.mark.asyncio
async def test_group_rebase_preserves_archive_window_then_escalates_on_fifth() -> None:
    """Group rebase uses 40 child findings as baseline and escalates on finding five."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    primary: IssueRow = _issue("group-rebase-primary", 35)
    child: IssueRow = _issue("group-rebase-child", 36)
    child.merged_into_id = primary.id
    child.times_seen = 40
    child.distinct_fingerprints = 40
    async with db.sessions.begin() as session:
        session.add_all((primary, child))
        session.add(
            IssueMergeRow(
                id=new_id(),
                primary_issue_id=primary.id,
                merged_issue_id=child.id,
                merged_by="member:triager",
                merged_at=NOW,
            )
        )
        session.add_all(tuple(_finding(child.id, index) for index in range(40)))
        await session.flush()
        await recompute_group_counters(session, primary)
        await _archive(session, primary, audit, activity)
        archived_at: datetime | None = primary.archived_at
        archive_until: datetime | None = primary.archive_until
        await rebase_archive(session, primary, RebaseScope.GROUP, NOW, ArchivePolicy())
        assert primary.archive_baseline == 40
        assert primary.times_seen == 40
        assert primary.archived_at == archived_at
        assert primary.archive_until == archive_until
        assert primary.status == IssueStatus.ARCHIVED.value
        assert events.events == []
        session.add_all(tuple(_finding(child.id, index) for index in range(40, 44)))
        await session.flush()
        escalated: bool = await evaluate_after_ingest(
            session, primary, Visibility.UNKNOWN, NOW, ArchivePolicy(), audit, events, activity
        )
        assert escalated is False
        assert primary.status == IssueStatus.ARCHIVED.value
        assert primary.times_seen == 44
        session.add(_finding(child.id, 44))
        await session.flush()
        escalated = await evaluate_after_ingest(
            session, primary, Visibility.UNKNOWN, NOW, ArchivePolicy(), audit, events, activity
        )
        assert escalated is True
        assert primary.status == IssueStatus.ESCALATING.value
    assert len(events.events) == 1
    assert events.events[0].kind is IssueEventKind.ISSUE_ESCALATING
    await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finding_batches", "expected_threshold"),
    (
        (((timedelta(days=-30), 40),), 10),
        (((timedelta(days=-2), 4), (timedelta(hours=-1), 3)), 12),
    ),
    ids=("ignores-findings-older-than-seven-days", "uses-peak-and-trailing-counts"),
)
async def test_until_escalating_rebase_uses_the_seven_day_rate_window(
    finding_batches: tuple[tuple[timedelta, int], ...], expected_threshold: int
) -> None:
    """Rebase uses only the recent peak and trailing count without changing archive state."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("rate-rebase", 40)
    async with db.sessions.begin() as session:
        session.add(issue)
        await session.flush()
        await archive_issue(
            session,
            issue,
            ArchiveRequest(ArchiveKind.UNTIL_ESCALATING),
            "member:triager",
            NOW,
            ArchivePolicy(),
            audit,
            activity,
        )
        findings: list[FindingRow] = []
        finding_index: int = 0
        for offset, count in finding_batches:
            for _ in range(count):
                findings.append(_finding(issue.id, finding_index, NOW + offset))
                finding_index += 1
        session.add_all(findings)
        await session.flush()
        archived_at: datetime | None = issue.archived_at
        archive_until: datetime | None = issue.archive_until
        archived_status: str = issue.status
        await rebase_archive(session, issue, RebaseScope.OWN, NOW, ArchivePolicy())
        assert issue.archive_threshold == expected_threshold
        assert issue.archived_at == archived_at
        assert issue.archive_until == archive_until
        assert issue.status == archived_status == IssueStatus.ARCHIVED.value
        assert events.events == []
    await db.close()
