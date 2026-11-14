"""Regression tests for v1.3 bulk issue actions and group merge safety."""

from datetime import datetime, timedelta

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from sqlalchemy import select

from telescreen.api.common import ApiContext, PermissionDependency
from telescreen.api.routers.issue_bulk import IssueBulkRouterDeps, build_issue_bulk_router
from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import (
    ArchiveKind,
    ErrorCode,
    IssueResolution,
    IssueStatus,
    RemediationAction,
)
from telescreen.db.models import IssueExceptionRow, IssueHistoryRow, IssueRow
from telescreen.directory import DirectoryRecipientResolver
from telescreen.issues.archive import ArchivePolicy, ArchiveRequest, archive_issue
from telescreen.issues.bulk import (
    BulkAction,
    BulkArchiveParams,
    BulkParams,
    BulkRequest,
    BulkResult,
    BulkService,
    BulkVerificationParams,
    validate_bulk_params,
)
from telescreen.issues.groups import active_children
from telescreen.issues.lifecycle import IssueRemediationService
from telescreen.issues.merge import MergeService
from telescreen.issues.query.filters import QueryContext
from telescreen.issues.query.period import Period, parse_period
from telescreen.issues.service import IssueService
from telescreen.ports.audit import AuditEntry
from tests.issues.v13_lifecycle.test_archive import (
    NOW,
    RecordingActivity,
    RecordingAudit,
    RecordingEvents,
    RecordingIndexer,
    _finding,
    _issue,
    _world,
)
from tests.issues.v13_lifecycle.test_archive_exits import _services
from tests.support.db import SqliteHarness
from tests.support.samples import Sample, github_token


@pytest.mark.asyncio
async def test_bulk_status_uses_single_issue_services_and_writes_count_only_audit() -> None:
    """Bulk status commits independently and records one count-only summary audit."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issues: tuple[IssueRow, ...] = (
        _issue("bulk-1", 1),
        _issue("bulk-2", 2),
        _issue("bulk-3", 3, IssueStatus.RESOLVED),
    )
    async with db.sessions.begin() as session:
        session.add_all(issues)
    issue_service: IssueService
    remediation: IssueRemediationService
    issue_service, _, remediation = _services(audit, events, activity)
    indexer: RecordingIndexer = RecordingIndexer([])
    bulk: BulkService = BulkService(
        db.sessions, issue_service, remediation, ArchivePolicy(), FakeClock(NOW), audit, indexer
    )
    request: BulkRequest = BulkRequest(
        tuple(issue.id for issue in issues),
        None,
        None,
        BulkAction.STATUS,
        BulkParams(status=IssueStatus.ACKNOWLEDGED),
    )
    result: BulkResult = await bulk.run(
        request,
        "member:triager",
        False,
        QueryContext(None, DirectoryRecipientResolver(), None, NOW),
    )
    assert result.matched == result.processed == 3
    assert result.succeeded == ("bulk-1", "bulk-2")
    assert len(result.failed) == 1
    assert result.failed[0].issue_id == "bulk-3"
    assert result.failed[0].code is ErrorCode.INVALID_TRANSITION
    async with db.sessions() as session:
        rows: tuple[IssueRow, ...] = tuple((await session.scalars(select(IssueRow))).all())
        history: tuple[IssueHistoryRow, ...] = tuple(
            (await session.scalars(select(IssueHistoryRow))).all()
        )
    assert sum(row.status == IssueStatus.ACKNOWLEDGED.value for row in rows) == 2
    assert sum(row.status == IssueStatus.RESOLVED.value for row in rows) == 1
    assert len(history) == 2
    assert len([entry for entry in audit.entries if entry.action == "issue.bulk"]) == 1
    summary: AuditEntry = next(entry for entry in audit.entries if entry.action == "issue.bulk")
    assert summary.details.member_names() == (
        "action",
        "matched",
        "processed",
        "succeeded",
        "failed",
    )
    assert indexer.issue_ids == ["bulk-1", "bulk-2"]
    await db.close()


def test_archive_until_escalating_accepts_no_duration_and_requires_kind() -> None:
    """Archive parsing rejects a duration for until_escalating and missing conditions."""
    with pytest.raises(TelescreenError) as duration_error:
        validate_bulk_params(
            BulkAction.ARCHIVE,
            BulkParams(
                archive=BulkArchiveParams(ArchiveKind.UNTIL_ESCALATING, duration_minutes=60)
            ),
        )
    assert duration_error.value.code is ErrorCode.VALIDATION_ERROR
    with pytest.raises(TelescreenError) as missing_error:
        validate_bulk_params(BulkAction.ARCHIVE, BulkParams())
    assert missing_error.value.code is ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_archive_until_escalating_has_90_day_bound_and_threshold() -> None:
    """Archive parsing stores the bounded escalation condition without a duration."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("bulk-archive", 4)
    async with db.sessions.begin() as session:
        session.add(issue)
    issue_service: IssueService
    remediation: IssueRemediationService
    issue_service, _, remediation = _services(audit, events, activity)
    bulk: BulkService = BulkService(
        db.sessions,
        issue_service,
        remediation,
        ArchivePolicy(min_findings=10, factor=3),
        FakeClock(NOW),
        audit,
        RecordingIndexer([]),
    )
    result: BulkResult = await bulk.run(
        BulkRequest(
            (issue.id,),
            None,
            None,
            BulkAction.ARCHIVE,
            BulkParams(archive=BulkArchiveParams(ArchiveKind.UNTIL_ESCALATING)),
        ),
        "member:triager",
        False,
        QueryContext(None, DirectoryRecipientResolver(), None, NOW),
    )
    assert result.succeeded == (issue.id,)
    async with db.sessions() as session:
        stored: IssueRow | None = await session.get(IssueRow, issue.id)
    assert stored is not None
    assert stored.status == IssueStatus.ARCHIVED.value
    assert stored.archive_kind == ArchiveKind.UNTIL_ESCALATING.value
    assert stored.archive_until is not None
    assert ensure_utc(stored.archive_until) == NOW + timedelta(days=90)
    assert stored.archive_threshold == 10
    await db.close()


@pytest.mark.asyncio
async def test_int_w2_04_custom_period_bulk_matches_list_scope_and_rejects_missing_dates() -> None:
    """INT-W2-04: custom-period bulk uses the issue list time scope and validates dates."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    inside: IssueRow = _issue("custom-in", 20)
    inside.source = "slack"
    outside: IssueRow = _issue("custom-out", 21)
    outside.source = "slack"
    outside.first_seen = outside.last_seen = NOW - timedelta(days=20)
    async with db.sessions.begin() as session:
        session.add_all((inside, outside))
    issue_service: IssueService
    remediation: IssueRemediationService
    issue_service, _, remediation = _services(audit, events, activity)
    bulk: BulkService = BulkService(
        db.sessions,
        issue_service,
        remediation,
        ArchivePolicy(),
        FakeClock(NOW),
        audit,
        RecordingIndexer([]),
    )
    selected_period: Period = parse_period("custom", NOW - timedelta(days=12), NOW, NOW)
    result: BulkResult = await bulk.run(
        BulkRequest(None, "source:slack", selected_period, BulkAction.MARK_REVIEWED, BulkParams()),
        "member:triager",
        False,
        QueryContext(None, DirectoryRecipientResolver(), None, NOW),
    )
    assert result.succeeded == (inside.id,)
    with pytest.raises(TelescreenError) as invalid_period:
        parse_period("custom", None, None, NOW)
    assert invalid_period.value.code is ErrorCode.VALIDATION_ERROR
    async with db.sessions() as session:
        stored_outside: IssueRow | None = await session.get(IssueRow, outside.id)
    assert stored_outside is not None and stored_outside.needs_review is False
    await db.close()


@pytest.mark.asyncio
async def test_int_w2_05_query_mode_requests_verification_for_all_matching_issues() -> None:
    """INT-W2-05: all-matching request_verification changes eligible issues in query mode."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("verify-bulk", 22)
    issue.source = "slack"
    async with db.sessions.begin() as session:
        session.add(issue)
    issue_service: IssueService
    remediation: IssueRemediationService
    issue_service, _, remediation = _services(audit, events, activity)
    bulk: BulkService = BulkService(
        db.sessions,
        issue_service,
        remediation,
        ArchivePolicy(),
        FakeClock(NOW),
        audit,
        RecordingIndexer([]),
    )
    result: BulkResult = await bulk.run(
        BulkRequest(
            None,
            "source:slack",
            Period(None, None),
            BulkAction.REQUEST_VERIFICATION,
            BulkParams(verification=BulkVerificationParams(RemediationAction.REMOVE, False)),
        ),
        "member:triager",
        False,
        QueryContext(None, DirectoryRecipientResolver(), None, NOW),
    )
    assert result.succeeded == (issue.id,)
    async with db.sessions() as session:
        stored: IssueRow | None = await session.get(IssueRow, issue.id)
    assert stored is not None and stored.status == IssueStatus.PENDING_VERIFICATION.value
    await db.close()


@pytest.mark.asyncio
async def test_query_bulk_caps_at_1000_of_1002_matching_issues() -> None:
    """Query-mode bulk reports all matches while processing only the first 1000."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issues: tuple[IssueRow, ...] = tuple(
        _issue(f"bulk-cap-{index}", index) for index in range(1, 1003)
    )
    for issue in issues:
        issue.source = "slack"
    async with db.sessions.begin() as session:
        session.add_all(issues)
    issue_service: IssueService
    remediation: IssueRemediationService
    issue_service, _, remediation = _services(audit, events, activity)
    bulk: BulkService = BulkService(
        db.sessions,
        issue_service,
        remediation,
        ArchivePolicy(),
        FakeClock(NOW),
        audit,
        RecordingIndexer([]),
    )
    result: BulkResult = await bulk.run(
        BulkRequest(
            None, "source:slack", Period(None, None), BulkAction.MARK_REVIEWED, BulkParams()
        ),
        "member:triager",
        False,
        QueryContext(None, DirectoryRecipientResolver(), None, NOW),
    )
    assert result.matched == 1002
    assert result.processed == 1000
    assert result.truncated is True
    assert len(result.succeeded) == 1000
    await db.close()


@pytest.mark.asyncio
async def test_manual_unverified_bulk_resolution_requires_permission_before_change() -> None:
    """Manual-unverified bulk resolve returns forbidden before changing any issue."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("manual-unverified", 23)
    async with db.sessions.begin() as session:
        session.add(issue)
    issue_service: IssueService
    remediation: IssueRemediationService
    issue_service, _, remediation = _services(audit, events, activity)
    bulk: BulkService = BulkService(
        db.sessions,
        issue_service,
        remediation,
        ArchivePolicy(),
        FakeClock(NOW),
        audit,
        RecordingIndexer([]),
    )
    with pytest.raises(TelescreenError) as captured:
        await bulk.run(
            BulkRequest(
                (issue.id,),
                None,
                None,
                BulkAction.RESOLVE,
                BulkParams(reason=IssueResolution.MANUAL_UNVERIFIED, note="Reviewed"),
            ),
            "member:responder",
            False,
            QueryContext(None, DirectoryRecipientResolver(), None, NOW),
        )
    assert captured.value.code is ErrorCode.FORBIDDEN
    async with db.sessions() as session:
        stored: IssueRow | None = await session.get(IssueRow, issue.id)
    assert stored is not None and stored.status == IssueStatus.UNRESOLVED.value
    await db.close()


@pytest.mark.asyncio
async def test_bulk_resolve_redacts_note_before_history_and_audit() -> None:
    """Bulk resolve delegates note masking and never persists a raw sample."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("masked-bulk-note", 24)
    sample: Sample = github_token()
    async with db.sessions.begin() as session:
        session.add(issue)
    issue_service: IssueService
    remediation: IssueRemediationService
    issue_service, _, remediation = _services(audit, events, activity)
    bulk: BulkService = BulkService(
        db.sessions,
        issue_service,
        remediation,
        ArchivePolicy(),
        FakeClock(NOW),
        audit,
        RecordingIndexer([]),
    )
    result: BulkResult = await bulk.run(
        BulkRequest(
            (issue.id,),
            None,
            None,
            BulkAction.RESOLVE,
            BulkParams(reason=IssueResolution.FALSE_POSITIVE, note=sample.value),
        ),
        "member:triager",
        False,
        QueryContext(None, DirectoryRecipientResolver(), None, NOW),
    )
    assert result.succeeded == (issue.id,)
    async with db.sessions() as session:
        history: tuple[IssueHistoryRow, ...] = tuple(
            (
                await session.scalars(
                    select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue.id)
                )
            ).all()
        )
    assert history
    assert all(sample.value not in (row.note or "") for row in history)
    assert all(sample.value not in str(entry.details) for entry in audit.entries)
    assert sample.value not in repr(result)
    await db.close()


@pytest.mark.asyncio
async def test_bulk_router_has_exactly_one_write_permission_per_route() -> None:
    """All three bulk routes declare exactly one ISSUES_WRITE dependency."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue_service: IssueService
    remediation: IssueRemediationService
    issue_service, _, remediation = _services(audit, events, activity)
    resolver: DirectoryRecipientResolver = DirectoryRecipientResolver()
    indexer: RecordingIndexer = RecordingIndexer([])
    bulk: BulkService = BulkService(
        db.sessions, issue_service, remediation, ArchivePolicy(), FakeClock(NOW), audit, indexer
    )
    merge: MergeService = MergeService(db.sessions, audit, FakeClock(NOW), indexer, activity)
    ctx: ApiContext = ApiContext(db.sessions, FakeClock(NOW), audit)
    router: APIRouter = build_issue_bulk_router(
        IssueBulkRouterDeps(ctx, bulk, merge, indexer, resolver)
    )
    routes: tuple[APIRoute, ...] = tuple(
        route for route in router.routes if isinstance(route, APIRoute)
    )
    assert len(routes) == 3
    for route in routes:
        permission_dependencies: tuple[PermissionDependency, ...] = tuple(
            dependency.call
            for dependency in route.dependant.dependencies
            if isinstance(dependency.call, PermissionDependency)
        )
        assert len(permission_dependencies) == 1
        assert permission_dependencies[0].permission is Permission.ISSUES_WRITE
    await db.close()


@pytest.mark.asyncio
async def test_merge_rejects_requested_exception_without_changes() -> None:
    """A requested exception prevents merging and leaves both issues untouched."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    primary: IssueRow = _issue("exception-primary", 30)
    child: IssueRow = _issue("exception-child", 31)
    async with db.sessions.begin() as session:
        session.add_all((primary, child))
        session.add(
            IssueExceptionRow(
                id=new_id(),
                issue_id=child.id,
                reason="safe reason",
                duration="7d",
                status="requested",
                requested_by="member:triager",
                approver_ref=None,
                decided_by=None,
                decided_at=None,
                expires_at=None,
                created_at=NOW,
            )
        )
    merge: MergeService = MergeService(
        db.sessions, audit, FakeClock(NOW), RecordingIndexer([]), activity
    )
    with pytest.raises(TelescreenError) as captured:
        await merge.merge(primary.id, (child.id,), "member:triager")
    assert captured.value.code is ErrorCode.MERGED_GROUP
    async with db.sessions() as session:
        stored_primary: IssueRow | None = await session.get(IssueRow, primary.id)
        stored_child: IssueRow | None = await session.get(IssueRow, child.id)
    assert stored_primary is not None and stored_primary.merged_into_id is None
    assert stored_child is not None and stored_child.merged_into_id is None
    await db.close()


@pytest.mark.asyncio
async def test_merge_rebases_archived_group_without_escalation_and_unmerge_rebases_child() -> None:
    """Safety rule 5: merge and unmerge preserve archive time and never escalate."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    primary: IssueRow = _issue("merge-primary", 10)
    child: IssueRow = _issue("merge-child", 11)
    child.times_seen = 40
    async with db.sessions.begin() as session:
        session.add_all((primary, child))
        session.add_all(tuple(_finding(child.id, index) for index in range(40)))
        await session.flush()
        await archive_issue(
            session,
            primary,
            ArchiveRequest(ArchiveKind.UNTIL_FINDINGS, count=5),
            "member:triager",
            NOW,
            ArchivePolicy(),
            audit,
            activity,
        )
    archive_until: datetime | None = primary.archive_until
    assert archive_until is not None
    merge: MergeService = MergeService(
        db.sessions, audit, FakeClock(NOW), RecordingIndexer([]), activity
    )
    merged: IssueRow = await merge.merge(primary.id, (child.id,), "member:triager")
    assert merged.status == IssueStatus.ARCHIVED.value
    assert merged.archive_until is not None
    assert merged.times_seen == 40
    assert merged.archive_baseline == 40
    assert ensure_utc(merged.archive_until) == ensure_utc(archive_until)
    assert events.events == []
    async with db.sessions() as session:
        active: tuple[IssueRow, ...] = await active_children(session, primary.id)
    assert tuple(issue.id for issue in active) == (child.id,)
    await merge.unmerge(primary.id, (child.id,), "member:triager")
    async with db.sessions() as session:
        stored_child: IssueRow | None = await session.get(IssueRow, child.id)
        stored_primary: IssueRow | None = await session.get(IssueRow, primary.id)
    assert stored_child is not None and stored_child.needs_review is True
    assert stored_child.archive_baseline == 40
    assert stored_primary is not None
    assert stored_primary.archive_until is not None
    assert ensure_utc(stored_primary.archive_until) == ensure_utc(archive_until)
    assert events.events == []
    await db.close()
