"""Archive bounds, escalation, lifecycle clearing and triage route tests."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import (
    ApiContext,
    PermissionDependency,
    Principal,
    install_error_handlers,
)
from telescreen.api.routers.issue_triage import IssueTriageRouterDeps, build_issue_triage_router
from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import (
    ArchiveKind,
    DetectorKind,
    ErrorCode,
    ExceptionDuration,
    IssueEventKind,
    IssueHistoryKind,
    IssuePriority,
    IssueStatus,
    PolicyEffect,
    PrincipalKind,
    Role,
    RuleClass,
    Severity,
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
    ArchiveMaintenanceProvider,
    ArchivePolicy,
    ArchiveRequest,
    RebaseScope,
    archive_issue,
    evaluate_after_ingest,
    rebase_archive,
    unarchive_issue,
    validate_archive_request,
)
from telescreen.issues.exceptions import ExceptionService
from telescreen.issues.groups import GroupAction, GroupChange, cascade_to_children
from telescreen.issues.service import IssueService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.findings import MaskedFinding, ResourceScan
from telescreen.ports.issues import (
    FindingsRecorded,
    IssueActivity,
    IssueEvent,
)
from telescreen.ports.policy import PolicyDecision, PolicySubject
from tests.support.db import SqliteHarness, make_sqlite_harness

NOW: datetime = datetime(2026, 10, 12, tzinfo=UTC)


@dataclass
class RecordingAudit:
    """Capture count-only lifecycle audit entries."""

    entries: list[AuditEntry]

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Capture an audit entry without external writes."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class RecordingEvents:
    """Capture issue events emitted within a transaction."""

    events: list[IssueEvent]

    async def on_issue_event(self, session: AsyncSession, event: IssueEvent) -> None:
        """Record a workflow event."""
        del session
        self.events.append(event)


@dataclass
class RecordingActivity:
    """Capture safe issue activity DTOs."""

    activities: list[IssueActivity]

    async def on_activity(self, session: AsyncSession, activity: IssueActivity) -> None:
        """Record activity without sending notifications."""
        del session
        self.activities.append(activity)


@dataclass
class RecordingFindingsObserver:
    """Capture count-only finding notifications."""

    events: list[FindingsRecorded]

    async def on_findings_recorded(self, session: AsyncSession, event: FindingsRecorded) -> None:
        """Record an inserted finding count without content."""
        del session
        self.events.append(event)


@dataclass
class RecordingIndexer:
    """Capture post-commit issue indexing requests."""

    issue_ids: list[str]

    async def index_issue(self, issue_id: str) -> None:
        """Record an issue id for indexing."""
        self.issue_ids.append(issue_id)

    async def index_finding(self, finding_id: str) -> None:
        """Satisfy the search indexer protocol."""
        del finding_id

    async def index_run(self, run_id: str) -> None:
        """Satisfy the search indexer protocol."""
        del run_id


@dataclass(frozen=True)
class StaticPolicyDecider:
    """Return a stable policy result if the exception request reaches routing."""

    async def decide(
        self, session: AsyncSession, subject: PolicySubject, now: datetime
    ) -> PolicyDecision:
        """Satisfy the policy port with a safe no-op result."""
        del session, subject, now
        return PolicyDecision(PolicyEffect.VIOLATE, None, None, None, False)


def _issue(issue_id: str, number: int, status: IssueStatus = IssueStatus.UNRESOLVED) -> IssueRow:
    """Construct a minimal issue row for lifecycle tests."""
    return IssueRow(
        id=issue_id,
        number=number,
        grouping_key=f"group-{issue_id}",
        source="custom:test",
        resource_key=f"resource-{issue_id}",
        display_locator="masked locator",
        display_title="masked title",
        rule_class=RuleClass.CREDENTIAL.value,
        severity=Severity.HIGH.value,
        status=status.value,
        first_seen=NOW,
        last_seen=NOW,
        last_checked_at=NOW,
        sweep_covered=False,
        times_seen=0,
        distinct_fingerprints=0,
        regression_count=0,
        resolved_at=None,
        resolution=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _finding(issue_id: str, index: int, observed_at: datetime = NOW) -> FindingRow:
    """Construct one masked finding with a distinct keyed fingerprint."""
    return FindingRow(
        id=new_id(),
        issue_id=issue_id,
        scan_job_id=f"scan-{index}",
        source="custom:test",
        resource_key=f"resource-{issue_id}",
        display_locator="masked locator",
        location="segment:1",
        rule_id="test.rule",
        rule_class=RuleClass.CREDENTIAL.value,
        detector="rule",
        confidence=0.99,
        masked_snippet="[REDACTED]",
        fingerprint=f"fingerprint-{index}",
        fingerprint_key_id="test-key",
        observed_at=observed_at,
        fingerprint_key_version=1,
    )


async def _world() -> tuple[SqliteHarness, RecordingAudit, RecordingEvents, RecordingActivity]:
    """Create isolated SQLite storage and lifecycle recorders."""
    return (
        await make_sqlite_harness(),
        RecordingAudit([]),
        RecordingEvents([]),
        RecordingActivity([]),
    )


@pytest.mark.parametrize(
    "archive_request",
    (
        ArchiveRequest(ArchiveKind.UNTIL_FINDINGS, count=0),
        ArchiveRequest(ArchiveKind.UNTIL_VALUES, count=100001),
        ArchiveRequest(ArchiveKind.FOR_DURATION, duration_minutes=200000),
        ArchiveRequest(ArchiveKind.UNTIL_ESCALATING, count=1),
    ),
)
def test_archive_request_rejects_invalid_parameters(archive_request: ArchiveRequest) -> None:
    """Every archive condition rejects invalid or irrelevant parameters."""
    with pytest.raises(TelescreenError) as captured:
        validate_archive_request(archive_request)
    assert captured.value.code is ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_until_findings_escalates_and_clears_bounded_archive() -> None:
    """The fifth new finding escalates and clears every archive field."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("issue-primary", 1)
    async with db.sessions.begin() as session:
        session.add(issue)
        await session.flush()
        await archive_issue(
            session,
            issue,
            ArchiveRequest(ArchiveKind.UNTIL_FINDINGS, count=5),
            "member:triager",
            NOW,
            ArchivePolicy(),
            audit,
            activity,
        )
        assert issue.archive_until is not None
        assert issue.archive_until <= NOW + timedelta(days=90)
        for index in range(4):
            session.add(_finding(issue.id, index))
        remains_archived: bool = await evaluate_after_ingest(
            session, issue, Visibility.UNKNOWN, NOW, ArchivePolicy(), audit, events, activity
        )
        assert remains_archived is False
        assert issue.status == IssueStatus.ARCHIVED.value
        session.add(_finding(issue.id, 4))
        escalated: bool = await evaluate_after_ingest(
            session, issue, Visibility.UNKNOWN, NOW, ArchivePolicy(), audit, events, activity
        )
        assert escalated is True
        assert issue.status == IssueStatus.ESCALATING.value
        assert issue.needs_review is True
        assert (
            issue.archive_kind,
            issue.archive_until,
            issue.archive_threshold,
            issue.archive_baseline,
            issue.archived_at,
        ) == (None, None, None, None, None)
    assert len(events.events) == 1
    assert events.events[0].kind is IssueEventKind.ISSUE_ESCALATING
    assert activity.activities[-1].kind is IssueHistoryKind.ESCALATED
    await db.close()


@pytest.mark.asyncio
async def test_archive_never_writes_suppression_or_exception_state() -> None:
    """Archiving stays distinct from ignored, allowed, and approved exceptions."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("issue-safe-archive", 7)
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
        exceptions: tuple[IssueExceptionRow, ...] = tuple(
            (await session.scalars(select(IssueExceptionRow))).all()
        )
        assert exceptions == ()
        assert issue.status == IssueStatus.ARCHIVED.value
        assert issue.status not in (IssueStatus.IGNORED.value, IssueStatus.ALLOWED.value)
    await db.close()


@pytest.mark.asyncio
async def test_automatic_resolution_clears_archive_metadata() -> None:
    """The ingest auto-resolve exit from archived uses clear_archive."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    fingerprinter: Fingerprinter = Fingerprinter(b"t" * 32, "test-key")
    service: IssueService = IssueService(
        audit,
        events,
        fingerprinter,
        Redactor.default(),
        DirectoryRecipientResolver(),
        FakeClock(NOW),
        activity=activity,
    )
    issue: IssueRow = _issue("auto-resolve-archive", 14, IssueStatus.ARCHIVED)
    issue.archive_kind = ArchiveKind.FOR_DURATION.value
    issue.archived_at = NOW
    issue.archive_until = NOW + timedelta(days=1)
    issue.archive_threshold = None
    issue.archive_baseline = None
    resource_key: str = fingerprinter.resource_key("custom:test", "resource:auto")
    issue.resource_key = resource_key
    scan: ResourceScan = ResourceScan(
        "custom:test",
        resource_key,
        "resource:auto",
        "Masked resource",
        None,
        "scan-empty",
        None,
        (),
        True,
        False,
        NOW + timedelta(minutes=1),
    )
    async with db.sessions.begin() as session:
        session.add(issue)
    async with db.sessions.begin() as session:
        await service.record_scan(session, scan)
        stored: IssueRow | None = await session.get(IssueRow, issue.id)
        assert stored is not None
        assert stored.status == IssueStatus.RESOLVED.value
        assert (
            stored.archive_kind,
            stored.archive_until,
            stored.archive_threshold,
            stored.archive_baseline,
            stored.archived_at,
        ) == (None, None, None, None, None)
    await db.close()


@pytest.mark.asyncio
async def test_priority_review_and_findings_observer_follow_ingest() -> None:
    """Priority tracks severity until manual and review/observer flags are accurate."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    observer: RecordingFindingsObserver = RecordingFindingsObserver([])
    fingerprinter: Fingerprinter = Fingerprinter(b"t" * 32, "test-key")
    service: IssueService = IssueService(
        audit,
        events,
        fingerprinter,
        Redactor.default(),
        DirectoryRecipientResolver(),
        FakeClock(NOW),
        findings_observer=observer,
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
        fingerprinter.fingerprint("test.credential", "marker", RuleClass.CREDENTIAL),
        fingerprinter.key_id,
    )
    scan: ResourceScan = ResourceScan(
        "custom:test",
        fingerprinter.resource_key("custom:test", "resource:1"),
        "resource:1",
        "Masked resource",
        None,
        "scan-1",
        None,
        (finding,),
        False,
        False,
        NOW,
    )
    async with db.sessions.begin() as session:
        await service.record_scan(session, scan)
        issue: IssueRow = (await session.scalars(select(IssueRow))).one()
        assert issue.priority == "high"
        assert issue.priority_manual is False
        assert issue.needs_review is True
        await service.set_priority(session, issue.id, IssuePriority.LOW, "member:triager")
        issue.status = IssueStatus.RESOLVED.value
    scan_again: ResourceScan = ResourceScan(
        scan.source,
        scan.resource_key,
        scan.display_locator,
        scan.display_title,
        scan.collector_config_id,
        "scan-2",
        scan.ref_json,
        scan.findings,
        False,
        False,
        NOW + timedelta(minutes=1),
    )
    async with db.sessions.begin() as session:
        await service.record_scan(session, scan_again)
        issue = (await session.scalars(select(IssueRow))).one()
        assert issue.status == IssueStatus.REGRESSED.value
        assert issue.priority == "low"
        assert issue.priority_manual is True
        assert issue.needs_review is True
        await service.mark_reviewed(session, issue.id, "member:triager")
        history: tuple[IssueHistoryRow, ...] = tuple(
            (
                await session.scalars(
                    select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue.id)
                )
            ).all()
        )
        assert issue.needs_review is False
        assert sum(row.kind == IssueHistoryKind.PRIORITY_CHANGED.value for row in history) == 1
        assert sum(row.kind == IssueHistoryKind.REVIEWED.value for row in history) == 1
    assert tuple(event.inserted for event in observer.events) == (1, 1)
    assert activity.activities[0].kind is IssueHistoryKind.REGRESSED
    await db.close()


@pytest.mark.asyncio
async def test_unarchive_and_expiry_clear_metadata_and_mark_review() -> None:
    """Manual and scheduled unarchive return to unresolved with review required."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("issue-expiry", 2)
    async with db.sessions.begin() as session:
        session.add(issue)
        await session.flush()
        await archive_issue(
            session,
            issue,
            ArchiveRequest(ArchiveKind.FOR_DURATION, duration_minutes=60),
            "member:triager",
            NOW,
            ArchivePolicy(),
            audit,
            activity,
        )
        await unarchive_issue(session, issue, "member:triager", NOW, audit, activity)
        assert issue.status == IssueStatus.UNRESOLVED.value
        assert issue.archive_until is None
        assert issue.needs_review is False
        await archive_issue(
            session,
            issue,
            ArchiveRequest(ArchiveKind.FOR_DURATION, duration_minutes=60),
            "member:triager",
            NOW,
            ArchivePolicy(),
            audit,
            activity,
        )
    provider: ArchiveMaintenanceProvider = ArchiveMaintenanceProvider(audit, activity)
    async with db.sessions.begin() as session:
        early: int = await provider.tick(session, NOW + timedelta(minutes=59))
        assert early == 0
    async with db.sessions.begin() as session:
        expired: int = await provider.tick(session, NOW + timedelta(minutes=61))
        assert expired == 1
        row: IssueRow | None = await session.get(IssueRow, issue.id)
        assert row is not None
        assert row.status == IssueStatus.UNRESOLVED.value
        assert row.needs_review is True
        assert row.archive_until is None
        history: tuple[IssueHistoryRow, ...] = tuple(
            (
                await session.scalars(
                    select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue.id)
                )
            ).all()
        )
        assert any(
            entry.kind == IssueHistoryKind.UNARCHIVED.value and entry.actor_ref == "system:archive"
            for entry in history
        )
    await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", tuple(ArchiveKind))
async def test_rebase_changes_only_condition_counters(kind: ArchiveKind) -> None:
    """Group rebasing preserves archive status and its fixed 90-day window."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("issue-rebase", 3)
    async with db.sessions.begin() as session:
        session.add(issue)
        session.add(_finding(issue.id, 10))
        await session.flush()
        request: ArchiveRequest = (
            ArchiveRequest(ArchiveKind.FOR_DURATION, duration_minutes=60)
            if kind is ArchiveKind.FOR_DURATION
            else ArchiveRequest(kind, count=5)
            if kind in (ArchiveKind.UNTIL_FINDINGS, ArchiveKind.UNTIL_VALUES)
            else ArchiveRequest(kind)
        )
        await archive_issue(
            session,
            issue,
            request,
            "member:triager",
            NOW,
            ArchivePolicy(),
            audit,
            activity,
        )
        archived_at: datetime | None = issue.archived_at
        archive_until: datetime | None = issue.archive_until
        await rebase_archive(
            session, issue, RebaseScope.OWN, NOW + timedelta(days=1), ArchivePolicy()
        )
        assert issue.status == IssueStatus.ARCHIVED.value
        if kind in (ArchiveKind.UNTIL_FINDINGS, ArchiveKind.UNTIL_VALUES):
            assert issue.archive_baseline == 1
        if issue.archive_until is not None:
            assert ensure_utc(issue.archive_until) <= NOW + timedelta(days=90)
        assert issue.archived_at == archived_at
        assert issue.archive_until == archive_until
        assert len(events.events) == 0
    await db.close()


@pytest.mark.asyncio
async def test_values_rate_and_exposure_conditions_escalate() -> None:
    """Distinct-value, rate, and widened-exposure conditions escalate archived issues."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    values_issue: IssueRow = _issue("issue-values", 9)
    rate_issue: IssueRow = _issue("issue-rate", 10)
    exposure_issue: IssueRow = _issue("issue-exposure", 11)
    exposure_issue.exposure_visibility = Visibility.INTERNAL.value
    async with db.sessions.begin() as session:
        session.add_all((values_issue, rate_issue, exposure_issue))
        await session.flush()
        for issue, request in (
            (values_issue, ArchiveRequest(ArchiveKind.UNTIL_VALUES, count=2)),
            (rate_issue, ArchiveRequest(ArchiveKind.UNTIL_ESCALATING)),
            (exposure_issue, ArchiveRequest(ArchiveKind.UNTIL_VALUES, count=100)),
        ):
            await archive_issue(
                session,
                issue,
                request,
                "member:triager",
                NOW,
                ArchivePolicy(),
                audit,
                activity,
            )
        assert rate_issue.archive_threshold == 10
        for index in range(9):
            session.add(_finding(rate_issue.id, index))
        for index in range(2):
            session.add(_finding(values_issue.id, index))
        assert (
            await evaluate_after_ingest(
                session,
                rate_issue,
                Visibility.UNKNOWN,
                NOW,
                ArchivePolicy(),
                audit,
                events,
                activity,
            )
        ) is False
        assert (
            await evaluate_after_ingest(
                session,
                values_issue,
                Visibility.UNKNOWN,
                NOW,
                ArchivePolicy(),
                audit,
                events,
                activity,
            )
        ) is True
        session.add(_finding(rate_issue.id, 9))
        assert (
            await evaluate_after_ingest(
                session,
                rate_issue,
                Visibility.UNKNOWN,
                NOW,
                ArchivePolicy(),
                audit,
                events,
                activity,
            )
        ) is True
        exposure_issue.exposure_visibility = Visibility.PUBLIC.value
        assert (
            await evaluate_after_ingest(
                session,
                exposure_issue,
                Visibility.INTERNAL,
                NOW,
                ArchivePolicy(),
                audit,
                events,
                activity,
            )
        ) is True
    assert tuple(event.kind for event in events.events) == (
        IssueEventKind.ISSUE_ESCALATING,
        IssueEventKind.ISSUE_ESCALATING,
        IssueEventKind.ISSUE_ESCALATING,
    )
    await db.close()


@pytest.mark.asyncio
async def test_group_cascade_returns_children_that_cannot_transition() -> None:
    """A group cascade reports children whose current status cannot transition."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    primary: IssueRow = _issue("primary", 4)
    open_child: IssueRow = _issue("child-open", 5)
    closed_child: IssueRow = _issue("child-closed", 6, IssueStatus.IGNORED)
    open_child.merged_into_id = primary.id
    closed_child.merged_into_id = primary.id
    async with db.sessions.begin() as session:
        session.add_all((primary, open_child, closed_child))
        session.add_all(
            (
                IssueMergeRow(
                    id=new_id(),
                    primary_issue_id=primary.id,
                    merged_issue_id=open_child.id,
                    merged_by="member:triager",
                    merged_at=NOW,
                ),
                IssueMergeRow(
                    id=new_id(),
                    primary_issue_id=primary.id,
                    merged_issue_id=closed_child.id,
                    merged_by="member:triager",
                    merged_at=NOW,
                ),
            )
        )
        await session.flush()
        skipped: tuple[str, ...] = await cascade_to_children(
            session,
            primary,
            GroupChange(
                GroupAction.STATUS,
                "member:triager",
                NOW,
                status=IssueStatus.ACKNOWLEDGED,
            ),
        )
        assert skipped == (closed_child.id,)
        assert open_child.status == IssueStatus.ACKNOWLEDGED.value
    await db.close()


@pytest.mark.asyncio
async def test_triage_routes_have_one_issues_write_dependency_each() -> None:
    """Archive, unarchive, and review each declare exactly one write permission."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    service: IssueService = IssueService(
        audit,
        events,
        Fingerprinter(b"t" * 32, "test-key"),
        Redactor.default(),
        DirectoryRecipientResolver(),
        FakeClock(NOW),
        activity=activity,
    )
    context: ApiContext = ApiContext(db.sessions, FakeClock(NOW), audit)
    router: APIRouter = build_issue_triage_router(
        IssueTriageRouterDeps(context, service, RecordingIndexer([]))
    )
    routes: tuple[APIRoute, ...] = tuple(
        route for route in router.routes if isinstance(route, APIRoute)
    )
    assert len(routes) == 3
    for route in routes:
        dependencies: tuple[Dependant, ...] = tuple(route.dependant.dependencies)
        permissions: tuple[PermissionDependency, ...] = tuple(
            dependency.call
            for dependency in dependencies
            if isinstance(dependency.call, PermissionDependency)
        )
        assert len(dependencies) == 1
        assert len(permissions) == 1
        assert permissions[0].permission is Permission.ISSUES_WRITE
    await db.close()


@pytest.mark.asyncio
async def test_triage_router_archives_unarchives_and_reviews_db_rows() -> None:
    """The dedicated routes mutate issue rows and history through one transaction."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    clock: FakeClock = FakeClock(NOW)
    service: IssueService = IssueService(
        audit,
        events,
        Fingerprinter(b"t" * 32, "test-key"),
        Redactor.default(),
        DirectoryRecipientResolver(),
        clock,
        activity=activity,
    )
    context: ApiContext = ApiContext(db.sessions, clock, audit)
    router: APIRouter = build_issue_triage_router(
        IssueTriageRouterDeps(context, service, RecordingIndexer([]))
    )
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    principal: Principal = Principal(
        PrincipalKind.OIDC, None, "triager", Role.ADMIN, frozenset((Permission.ISSUES_WRITE,))
    )

    async def allow_triage() -> Principal:
        """Supply one authorized principal to the three endpoint tests."""
        return principal

    for route in router.routes:
        if isinstance(route, APIRoute):
            dependency: Dependant = route.dependant.dependencies[0]
            assert isinstance(dependency.call, PermissionDependency)
            permission: PermissionDependency = dependency.call
            app.dependency_overrides[permission] = allow_triage
    issue: IssueRow = _issue("route-issue", 8)
    async with db.sessions.begin() as session:
        session.add(issue)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        archived: Response = await client.post(
            f"/api/issues/{issue.id}/archive",
            json={"kind": "until_findings", "count": 5},
        )
        assert archived.status_code == 200
        assert archived.json()["id"] == issue.id
        async with db.sessions() as session:
            stored: IssueRow | None = await session.get(IssueRow, issue.id)
            assert stored is not None
            assert stored.status == IssueStatus.ARCHIVED.value
            assert stored.archive_kind == ArchiveKind.UNTIL_FINDINGS.value
            assert stored.archive_until is not None
            assert ensure_utc(stored.archive_until) <= NOW + timedelta(days=90)
        unarchived: Response = await client.post(f"/api/issues/{issue.id}/unarchive")
        assert unarchived.status_code == 200
        async with db.sessions.begin() as session:
            stored = await session.get(IssueRow, issue.id)
            assert stored is not None
            assert stored.status == IssueStatus.UNRESOLVED.value
            assert stored.archive_until is None
            stored.needs_review = True
        reviewed: Response = await client.post(f"/api/issues/{issue.id}/review")
        assert reviewed.status_code == 200
        async with db.sessions() as session:
            stored = await session.get(IssueRow, issue.id)
            assert stored is not None
            assert stored.needs_review is False
            history: tuple[IssueHistoryRow, ...] = tuple(
                (
                    await session.scalars(
                        select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue.id)
                    )
                ).all()
            )
        assert any(row.kind == IssueHistoryKind.REVIEWED.value for row in history)
    await db.close()


@pytest.mark.asyncio
async def test_exception_request_rejects_merged_group_members() -> None:
    """Exception requests fail for both merged children and primaries."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    primary: IssueRow = _issue("exception-primary", 12)
    child: IssueRow = _issue("exception-child", 13)
    child.merged_into_id = primary.id
    service: ExceptionService = ExceptionService(
        audit,
        FakeClock(NOW),
        Redactor.default(),
        DirectoryOwnerResolver(),
        DirectoryRecipientResolver(),
        StaticPolicyDecider(),
    )
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
        await session.flush()
        for issue_id in (primary.id, child.id):
            with pytest.raises(TelescreenError) as captured:
                await service.request(
                    session,
                    issue_id,
                    "masked request reason",
                    ExceptionDuration.DAYS_7,
                    "member:triager",
                    False,
                )
            assert captured.value.code is ErrorCode.MERGED_GROUP
        assert tuple((await session.scalars(select(IssueExceptionRow))).all()) == ()
    await db.close()
