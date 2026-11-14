"""Merged issue, priority, review flag, and activity contracts."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import (
    ArchiveKind,
    DetectorKind,
    IssueEventKind,
    IssueHistoryKind,
    IssuePriority,
    IssueStatus,
    RuleClass,
    Severity,
    Visibility,
)
from telescreen.db.models import FindingRow, IssueHistoryRow, IssueMergeRow, IssueRow, MemberRow
from telescreen.directory import DirectoryRecipientResolver
from telescreen.issues.archive import ArchivePolicy, ArchiveRequest, archive_issue, unarchive_issue
from telescreen.issues.groups import recompute_group_counters
from telescreen.issues.keys import grouping_key
from telescreen.issues.service import IssueService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import Exposure
from telescreen.ports.findings import MaskedFinding, ResourceScan
from telescreen.ports.issues import IssueActivity
from tests.issues.v13_lifecycle.test_archive import (
    NOW,
    RecordingActivity,
    RecordingAudit,
    RecordingEvents,
    _finding,
    _issue,
    _world,
)
from tests.support.db import SqliteHarness


@pytest.mark.asyncio
async def test_reopened_merged_child_regresses_resolved_primary() -> None:
    """A violating scan that reopens a merged child regresses its resolved primary."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    fingerprinter: Fingerprinter = Fingerprinter(b"g" * 32, "test-key")
    primary: IssueRow = _issue("child-reopen-primary", 50, IssueStatus.RESOLVED)
    child: IssueRow = _issue("child-reopen-child", 51, IssueStatus.RESOLVED)
    child.resource_key = fingerprinter.resource_key("custom:test", "child-resource")
    child.grouping_key = grouping_key("custom:test", child.resource_key, RuleClass.CREDENTIAL)
    child.merged_into_id = primary.id
    service: IssueService = IssueService(
        audit,
        events,
        fingerprinter,
        Redactor.default(),
        DirectoryRecipientResolver(),
        FakeClock(NOW),
        activity=activity,
    )
    finding: MaskedFinding = MaskedFinding(
        "test.credential",
        RuleClass.CREDENTIAL,
        Severity.HIGH,
        DetectorKind.RULE,
        0.99,
        "[REDACTED]",
        "segment:1",
        fingerprinter.fingerprint("test.credential", "child-reopen", RuleClass.CREDENTIAL),
        fingerprinter.key_id,
    )
    scan: ResourceScan = ResourceScan(
        "custom:test",
        child.resource_key,
        "child-resource",
        "Masked child",
        None,
        "scan-child-reopen",
        None,
        (finding,),
        False,
        False,
        NOW + timedelta(minutes=1),
    )
    async with db.sessions.begin() as session:
        session.add_all((primary, child))
        session.add(
            IssueMergeRow(
                id="merge-child-reopen",
                primary_issue_id=primary.id,
                merged_issue_id=child.id,
                merged_by="member:triager",
                merged_at=NOW,
            )
        )
        await session.flush()
        await service.record_scan(session, scan)
        stored_primary: IssueRow | None = await session.get(IssueRow, primary.id)
        history: tuple[IssueHistoryRow, ...] = tuple(
            (
                await session.scalars(
                    select(IssueHistoryRow).where(IssueHistoryRow.issue_id == primary.id)
                )
            ).all()
        )
    assert stored_primary is not None
    assert stored_primary.status == IssueStatus.REGRESSED.value
    assert stored_primary.needs_review is True
    assert any(
        row.kind == IssueHistoryKind.REGRESSED.value and row.note == "via TELE-51"
        for row in history
    )
    assert any(event.kind is IssueEventKind.ISSUE_REGRESSED for event in events.events)
    await db.close()


@pytest.mark.asyncio
async def test_group_counters_equal_union_of_primary_and_child_findings() -> None:
    """Group counters count all findings and deduplicate shared fingerprints."""
    db: SqliteHarness = (await _world())[0]
    primary: IssueRow = _issue("counter-primary", 52)
    child: IssueRow = _issue("counter-child", 53)
    child.merged_into_id = primary.id
    earlier: datetime = NOW - timedelta(days=2)
    later: datetime = NOW + timedelta(minutes=2)
    primary_findings: tuple[FindingRow, ...] = (
        _finding(primary.id, 1, earlier),
        _finding(primary.id, 2, NOW),
        _finding(primary.id, 3, NOW),
    )
    child_findings: tuple[FindingRow, ...] = (
        _finding(child.id, 3, NOW),
        _finding(child.id, 4, NOW),
        _finding(child.id, 5, NOW),
        _finding(child.id, 6, later),
    )
    async with db.sessions.begin() as session:
        session.add_all((primary, child, *primary_findings, *child_findings))
        session.add(
            IssueMergeRow(
                id="merge-counter",
                primary_issue_id=primary.id,
                merged_issue_id=child.id,
                merged_by="member:triager",
                merged_at=NOW,
            )
        )
        await session.flush()
        await recompute_group_counters(session, primary)
        assert primary.times_seen == 7
        assert primary.distinct_fingerprints == 6
        assert primary.first_seen == earlier
        assert primary.last_seen == later
    await db.close()


@pytest.mark.asyncio
async def test_priority_follows_severity_until_manual_override() -> None:
    """Severity changes update automatic priority but preserve a manual priority."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    fingerprinter: Fingerprinter = Fingerprinter(b"v" * 32, "test-key")
    issue: IssueRow = _issue("priority-severity", 54)
    issue.rule_class = RuleClass.EMAIL.value
    issue.severity = Severity.MEDIUM.value
    issue.priority = IssuePriority.MEDIUM.value
    issue.resource_key = fingerprinter.resource_key("custom:test", "priority-resource")
    issue.grouping_key = grouping_key("custom:test", issue.resource_key, RuleClass.EMAIL)
    service: IssueService = IssueService(
        audit,
        events,
        fingerprinter,
        Redactor.default(),
        DirectoryRecipientResolver(),
        FakeClock(NOW),
        activity=activity,
    )
    finding: MaskedFinding = MaskedFinding(
        "test.email",
        RuleClass.EMAIL,
        Severity.MEDIUM,
        DetectorKind.RULE,
        0.99,
        "[REDACTED]",
        "segment:1",
        fingerprinter.fingerprint("test.email", "priority-severity", RuleClass.EMAIL),
        fingerprinter.key_id,
    )
    scan: ResourceScan = ResourceScan(
        "custom:test",
        issue.resource_key,
        "priority-resource",
        "Masked priority resource",
        None,
        "scan-priority-severity",
        None,
        (finding,),
        False,
        False,
        NOW,
    )
    async with db.sessions.begin() as session:
        session.add(issue)
        await session.flush()
        await service.record_scan(session, scan)
        assert issue.severity == Severity.MEDIUM.value
        assert issue.priority == IssuePriority.MEDIUM.value
        assert issue.priority_manual is False
        public_scan: ResourceScan = ResourceScan(
            scan.source,
            scan.resource_key,
            scan.display_locator,
            scan.display_title,
            scan.collector_config_id,
            "scan-priority-exposure",
            scan.ref_json,
            scan.findings,
            False,
            False,
            NOW + timedelta(minutes=1),
            exposure=Exposure(Visibility.PUBLIC),
        )
        await service.record_scan(session, public_scan)
        assert issue.severity == Severity.HIGH.value
        assert issue.priority == IssuePriority.HIGH.value
        assert issue.priority_manual is False
        await service.set_priority(session, issue.id, IssuePriority.LOW, "member:triager")
        await service.record_scan(
            session,
            ResourceScan(
                scan.source,
                scan.resource_key,
                scan.display_locator,
                scan.display_title,
                scan.collector_config_id,
                "scan-priority-manual",
                scan.ref_json,
                scan.findings,
                False,
                False,
                NOW + timedelta(minutes=2),
                exposure=Exposure(Visibility.PUBLIC),
            ),
        )
        assert issue.priority_manual is True
        assert issue.priority == IssuePriority.LOW.value
    await db.close()


@pytest.mark.asyncio
async def test_triage_clears_review_flag_and_emits_activity_payloads() -> None:
    """Assignment, status change, archive and unarchive update review/activity."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    assigned: IssueRow = _issue("review-assigned", 55)
    changed: IssueRow = _issue("review-status", 56)
    archived: IssueRow = _issue("review-archive", 57)
    assigned.needs_review = True
    changed.needs_review = True
    archived.needs_review = True
    service: IssueService = IssueService(
        audit,
        events,
        Fingerprinter(b"w" * 32, "test-key"),
        Redactor.default(),
        DirectoryRecipientResolver(),
        FakeClock(NOW),
        activity=activity,
    )
    async with db.sessions.begin() as session:
        session.add_all(
            (
                assigned,
                changed,
                archived,
                MemberRow(
                    id="triager-member",
                    handle="triager",
                    display_name="Triager",
                    email=None,
                    slack_user_id=None,
                    active=True,
                    created_at=NOW,
                    updated_at=NOW,
                    role="responder",
                ),
            )
        )
        await session.flush()
        await service.assign(session, assigned.id, "member:triager", "member:actor")
        await service.change_status(session, changed.id, IssueStatus.ACKNOWLEDGED, "member:actor")
        await archive_issue(
            session,
            archived,
            ArchiveRequest(ArchiveKind.FOR_DURATION, duration_minutes=60),
            "member:actor",
            NOW,
            ArchivePolicy(),
            audit,
            activity,
        )
        assert assigned.needs_review is False
        assert changed.needs_review is False
        assert archived.needs_review is False
        await unarchive_issue(session, archived, "member:actor", NOW, audit, activity)
        assert archived.status == IssueStatus.UNRESOLVED.value
    assigned_activity: IssueActivity = next(
        item for item in activity.activities if item.issue_id == assigned.id
    )
    status_activity: IssueActivity = next(
        item for item in activity.activities if item.issue_id == changed.id
    )
    unarchived_activity: IssueActivity = next(
        item
        for item in activity.activities
        if item.issue_id == archived.id and item.kind is IssueHistoryKind.UNARCHIVED
    )
    assert assigned_activity.kind is IssueHistoryKind.ASSIGNED
    assert assigned_activity.assignee_member_id == "triager-member"
    assert status_activity.kind is IssueHistoryKind.STATUS_CHANGED
    assert unarchived_activity.kind is IssueHistoryKind.UNARCHIVED
    await db.close()
