"""Plan-listed bulk and merge contract edge regressions."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.vocabulary import (
    ArchiveKind,
    ErrorCode,
    IssueEventKind,
    IssueStatus,
    Visibility,
)
from telescreen.db.models import IssueMergeRow, IssueRow
from telescreen.directory import DirectoryRecipientResolver
from telescreen.issues.archive import (
    ArchivePolicy,
    ArchiveRequest,
    archive_issue,
    evaluate_after_ingest,
)
from telescreen.issues.bulk import (
    BulkAction,
    BulkArchiveParams,
    BulkParams,
    BulkRequest,
    BulkResult,
    BulkService,
)
from telescreen.issues.groups import active_children
from telescreen.issues.lifecycle import IssueRemediationService
from telescreen.issues.merge import MergeService
from telescreen.issues.query.filters import QueryContext
from telescreen.issues.query.period import Period
from telescreen.issues.query.select import MatchResult, select_matching_ids
from telescreen.issues.service import IssueService
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


def _bulk_service(
    db: SqliteHarness,
    audit: RecordingAudit,
    events: RecordingEvents,
    activity: RecordingActivity,
) -> BulkService:
    """Build the real bulk service with the lifecycle test adapters."""
    issue_service: IssueService
    remediation: IssueRemediationService
    issue_service, _, remediation = _services(audit, events, activity)
    return BulkService(
        db.sessions,
        issue_service,
        remediation,
        ArchivePolicy(),
        FakeClock(NOW),
        audit,
        RecordingIndexer([]),
    )


def _query_context() -> QueryContext:
    """Return the shared empty-query context used by the list selector."""
    return QueryContext(None, DirectoryRecipientResolver(), None, NOW)


@pytest.mark.asyncio
async def test_bulk_selection_guards_reject_invalid_selection_before_changes() -> None:
    """Neither/both selections and 1001 ids fail before mutation or audit."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("selection-guard", 80)
    issue.needs_review = True
    async with db.sessions.begin() as session:
        session.add(issue)
    bulk: BulkService = _bulk_service(db, audit, events, activity)
    invalid_requests: tuple[BulkRequest, ...] = (
        BulkRequest(None, None, None, BulkAction.MARK_REVIEWED, BulkParams()),
        BulkRequest(
            (issue.id,), "source:slack", Period(None, None), BulkAction.MARK_REVIEWED, BulkParams()
        ),
        BulkRequest(
            tuple(f"missing-{index}" for index in range(1001)),
            None,
            None,
            BulkAction.MARK_REVIEWED,
            BulkParams(),
        ),
    )
    for request in invalid_requests:
        with pytest.raises(TelescreenError) as captured:
            await bulk.run(request, "member:triager", False, _query_context())
        assert captured.value.code is ErrorCode.VALIDATION_ERROR
    assert not any(entry.action == "issue.bulk" for entry in audit.entries)
    async with db.sessions() as session:
        stored: IssueRow | None = await session.get(IssueRow, issue.id)
    assert stored is not None and stored.needs_review is True
    await db.close()


@pytest.mark.asyncio
async def test_int_w2_05_query_verification_requires_parameters_before_change() -> None:
    """INT-W2-05: missing verification parameters fail without changing issues."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("verification-missing", 81)
    issue.source = "slack"
    async with db.sessions.begin() as session:
        session.add(issue)
    bulk: BulkService = _bulk_service(db, audit, events, activity)
    with pytest.raises(TelescreenError) as captured:
        await bulk.run(
            BulkRequest(
                None,
                "source:slack",
                Period(None, None),
                BulkAction.REQUEST_VERIFICATION,
                BulkParams(),
            ),
            "member:triager",
            False,
            _query_context(),
        )
    assert captured.value.code is ErrorCode.VALIDATION_ERROR
    assert not any(entry.action == "issue.bulk" for entry in audit.entries)
    async with db.sessions() as session:
        stored: IssueRow | None = await session.get(IssueRow, issue.id)
    assert stored is not None and stored.status == IssueStatus.UNRESOLVED.value
    await db.close()


@pytest.mark.asyncio
async def test_bulk_archive_until_findings_three_persists_bound() -> None:
    """Archive parsing: until_findings count three stores a bounded archive."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issues: tuple[IssueRow, ...] = (_issue("findings-bound-a", 82), _issue("findings-bound-b", 83))
    async with db.sessions.begin() as session:
        session.add_all(issues)
    bulk: BulkService = _bulk_service(db, audit, events, activity)
    result: BulkResult = await bulk.run(
        BulkRequest(
            tuple(issue.id for issue in issues),
            None,
            None,
            BulkAction.ARCHIVE,
            BulkParams(archive=BulkArchiveParams(ArchiveKind.UNTIL_FINDINGS, count=3)),
        ),
        "member:triager",
        False,
        _query_context(),
    )
    assert result.succeeded == tuple(issue.id for issue in issues)
    async with db.sessions() as session:
        stored: tuple[IssueRow, ...] = tuple(
            (await session.scalars(select(IssueRow).where(IssueRow.id.in_(result.succeeded)))).all()
        )
    assert len(stored) == 2
    assert all(row.status == IssueStatus.ARCHIVED.value for row in stored)
    assert all(row.archive_kind == ArchiveKind.UNTIL_FINDINGS.value for row in stored)
    assert all(row.archive_threshold == 3 for row in stored)
    for row in stored:
        assert row.archive_until is not None
        assert ensure_utc(row.archive_until) == NOW + timedelta(days=90)
    await db.close()


@pytest.mark.asyncio
async def test_bulk_mark_reviewed_clears_existing_review_flags() -> None:
    """Bulk mark_reviewed clears needs_review on every selected issue."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issues: tuple[IssueRow, ...] = (_issue("review-flag-a", 84), _issue("review-flag-b", 85))
    for issue in issues:
        issue.needs_review = True
    async with db.sessions.begin() as session:
        session.add_all(issues)
    result: BulkResult = await _bulk_service(db, audit, events, activity).run(
        BulkRequest(
            tuple(issue.id for issue in issues),
            None,
            None,
            BulkAction.MARK_REVIEWED,
            BulkParams(),
        ),
        "member:triager",
        False,
        _query_context(),
    )
    assert result.succeeded == tuple(issue.id for issue in issues)
    async with db.sessions() as session:
        stored: tuple[IssueRow, ...] = tuple(
            (await session.scalars(select(IssueRow).where(IssueRow.id.in_(result.succeeded)))).all()
        )
    assert len(stored) == 2 and all(row.needs_review is False for row in stored)
    await db.close()


@pytest.mark.asyncio
async def test_merge_hides_child_and_reparents_descendants_with_union_counters() -> None:
    """Merge hides children from list selection and reparents the complete group."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    primary: IssueRow = _issue("merge-union-primary", 86)
    child: IssueRow = _issue("merge-union-child", 87)
    target: IssueRow = _issue("merge-union-target", 88)
    async with db.sessions.begin() as session:
        session.add_all((primary, child, target))
        session.add_all(
            tuple(
                _finding(issue.id, index)
                for issue, count in ((primary, 3), (child, 8), (target, 2))
                for index in range(count)
            )
        )
    indexer: RecordingIndexer = RecordingIndexer([])
    merge: MergeService = MergeService(db.sessions, audit, FakeClock(NOW), indexer, activity)
    await merge.merge(primary.id, (child.id,), "member:triager")
    async with db.sessions() as session:
        selection: MatchResult = await select_matching_ids(
            session, "", Period(None, None), _query_context(), 1000
        )
        stored_primary: IssueRow | None = await session.get(IssueRow, primary.id)
    assert child.id not in selection.ids
    assert stored_primary is not None and stored_primary.times_seen == 11

    await merge.merge(target.id, (primary.id,), "member:triager")
    async with db.sessions() as session:
        stored_primary = await session.get(IssueRow, primary.id)
        stored_child: IssueRow | None = await session.get(IssueRow, child.id)
        stored_target: IssueRow | None = await session.get(IssueRow, target.id)
        children: tuple[IssueRow, ...] = await active_children(session, target.id)
    assert stored_primary is not None and stored_primary.merged_into_id == target.id
    assert stored_child is not None and stored_child.merged_into_id == target.id
    assert stored_target is not None and stored_target.times_seen == 13
    assert {item.id for item in children} == {primary.id, child.id}
    await db.close()


@pytest.mark.asyncio
async def test_safety_rule_five_later_findings_escalate_after_merge_on_fifth() -> None:
    """Safety rule 5: post-merge findings preserve archive then escalate on five."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    primary: IssueRow = _issue("post-merge-archive-primary", 89)
    child: IssueRow = _issue("post-merge-archive-child", 90)
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
    merge: MergeService = MergeService(
        db.sessions, audit, FakeClock(NOW), RecordingIndexer([]), activity
    )
    await merge.merge(primary.id, (child.id,), "member:triager")
    async with db.sessions() as session:
        stored_primary: IssueRow | None = await session.get(IssueRow, primary.id)
    assert stored_primary is not None and stored_primary.archive_until is not None
    archive_until: datetime = ensure_utc(stored_primary.archive_until)
    async with db.sessions.begin() as session:
        current_primary: IssueRow | None = await session.get(IssueRow, primary.id)
        assert current_primary is not None
        session.add_all(tuple(_finding(child.id, index) for index in range(40, 44)))
        await session.flush()
        escalated: bool = await evaluate_after_ingest(
            session,
            current_primary,
            Visibility.UNKNOWN,
            NOW,
            ArchivePolicy(),
            audit,
            events,
            activity,
        )
        assert escalated is False
        assert current_primary.status == IssueStatus.ARCHIVED.value
        assert current_primary.archive_until is not None
        assert ensure_utc(current_primary.archive_until) == archive_until
        session.add(_finding(child.id, 44))
        await session.flush()
        escalated = await evaluate_after_ingest(
            session,
            current_primary,
            Visibility.UNKNOWN,
            NOW,
            ArchivePolicy(),
            audit,
            events,
            activity,
        )
        assert escalated is True
        assert current_primary.status == IssueStatus.ESCALATING.value
    assert tuple(event.kind for event in events.events) == (IssueEventKind.ISSUE_ESCALATING,)
    await db.close()


@pytest.mark.asyncio
async def test_unmerge_non_child_rejects_without_changes() -> None:
    """Unmerging an unrelated issue returns validation_error without mutation."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    primary: IssueRow = _issue("unmerge-parent", 91)
    unrelated: IssueRow = _issue("unmerge-unrelated", 92)
    async with db.sessions.begin() as session:
        session.add_all((primary, unrelated))
    merge: MergeService = MergeService(
        db.sessions, audit, FakeClock(NOW), RecordingIndexer([]), activity
    )
    with pytest.raises(TelescreenError) as captured:
        await merge.unmerge(primary.id, (unrelated.id,), "member:triager")
    assert captured.value.code is ErrorCode.VALIDATION_ERROR
    assert not audit.entries
    async with db.sessions() as session:
        stored_primary: IssueRow | None = await session.get(IssueRow, primary.id)
        stored_unrelated: IssueRow | None = await session.get(IssueRow, unrelated.id)
    assert stored_primary is not None and stored_primary.merged_into_id is None
    assert stored_unrelated is not None and stored_unrelated.merged_into_id is None
    await db.close()


@pytest.mark.asyncio
async def test_merge_primary_already_merged_uses_precondition_three_contract() -> None:
    """Plan ambiguity: an already-merged primary returns MERGED_GROUP first."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    parent: IssueRow = _issue("ambiguous-parent", 93)
    child: IssueRow = _issue("ambiguous-child", 94)
    child.merged_into_id = parent.id
    async with db.sessions.begin() as session:
        session.add_all((parent, child))
        session.add(
            IssueMergeRow(
                id=new_id(),
                primary_issue_id=parent.id,
                merged_issue_id=child.id,
                merged_by="member:triager",
                merged_at=NOW,
            )
        )
    merge: MergeService = MergeService(
        db.sessions, audit, FakeClock(NOW), RecordingIndexer([]), activity
    )
    with pytest.raises(TelescreenError) as captured:
        await merge.merge(child.id, (parent.id,), "member:triager")
    assert captured.value.code is ErrorCode.MERGED_GROUP
    assert not audit.entries
    async with db.sessions() as session:
        stored_parent: IssueRow | None = await session.get(IssueRow, parent.id)
        stored_child: IssueRow | None = await session.get(IssueRow, child.id)
        child_rows: tuple[IssueRow, ...] = await active_children(session, parent.id)
    assert stored_parent is not None and stored_parent.merged_into_id is None
    assert stored_child is not None and stored_child.merged_into_id == parent.id
    assert tuple(item.id for item in child_rows) == (child.id,)
    await db.close()
