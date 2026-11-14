"""Behavioral tests for v1.1 collector coverage and health."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.collectors.coverage import (
    CoverageRecorder,
    CoverageResourceView,
    CoverageSummary,
    coverage_state,
)
from telescreen.collectors.health import (
    CollectorHealthProvider,
    CollectorHealthRecorder,
    CollectorHealthView,
    health_view,
)
from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.repository import CollectorRepository, ScanJobStoreImpl
from telescreen.collectors.rescanner import CollectorRescanner
from telescreen.collectors.sweep import CollectorSweeperImpl
from telescreen.collectors.tasks import CollectorScanHandler
from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import (
    CollectorKind,
    CoverageState,
    DetectorKind,
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
    HealthCondition,
    HealthStatus,
    IssueStatus,
    NotificationChannel,
    RuleClass,
    ScanJobStatus,
    ScanTrigger,
    Severity,
    TriggerMode,
    UnsupportedReason,
    Visibility,
)
from telescreen.db.models import (
    CollectorConfigRow,
    CollectorHealthRow,
    ResourceCoverageRow,
    ScanCoverageRow,
    ScanJobRow,
)
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    Coverage,
    EventCheck,
    EventCheckKind,
    EventResources,
    InboundWebhook,
    RescanResult,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    WatchState,
)
from telescreen.ports.findings import ExternalScan, RecordOutcome, ResourceScan
from telescreen.ports.issues import (
    IssueHistoryEntry,
    IssueSnapshot,
    OpenResourceRef,
)
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.ports.sweeps import (
    PartitionPage,
    PartitionRequest,
    ShardEntry,
    ShardEntryKind,
    ShardSpec,
    ShardSummary,
)
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskSpec
from tests.support.db import SqliteHarness, make_sqlite_harness

NOW: datetime = datetime(2026, 10, 10, 12, tzinfo=UTC)


@dataclass
class RescanAdapter:
    """Return incomplete content or simulate a deleted source resource."""

    content: ResourceContent
    gone: bool = False
    kind: CollectorKind = CollectorKind.SLACK

    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck:
        return EventCheck(EventCheckKind.VALID)

    async def resources_from_event(
        self, event: InboundWebhook, config: CollectorConfig
    ) -> EventResources:
        return EventResources((), ())

    async def list_changed(
        self, config: CollectorConfig, cursor: CollectorCursor | None
    ) -> ChangePage:
        return ChangePage((), (), cursor, False, False)

    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent:
        if self.gone:
            raise ResourceGoneError()
        return self.content

    async def renew_watch(
        self, config: CollectorConfig, current: WatchState | None
    ) -> WatchState | None:
        return None

    def ref_from_json(self, ref_json: str) -> ResourceRef:
        return ResourceRef(self.kind, ref_json, "keyed-locator")


@dataclass
class RescanReader:
    """Return one configured issue snapshot for the rescan service."""

    snapshot: IssueSnapshot

    async def get(self, session: AsyncSession, issue_id: str) -> IssueSnapshot | None:
        return self.snapshot if issue_id == self.snapshot.id else None

    async def open_resource_refs(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[OpenResourceRef, ...]:
        return ()


@dataclass
class RescanScanner:
    """Capture the resource content passed to the scanning pipeline."""

    content: list[ResourceContent] = field(default_factory=list)
    outcome: RecordOutcome = RecordOutcome.empty()

    async def scan_resource(
        self,
        content: ResourceContent,
        scan_job_id: str,
        detectors: tuple[DetectorKind, ...] | None = None,
    ) -> RecordOutcome:
        self.content.append(content)
        return self.outcome


@dataclass
class CapturingIssueRecorder:
    """Capture scan records while satisfying the issue recorder port."""

    scans: list[ResourceScan] = field(default_factory=list)
    snapshot: IssueSnapshot | None = None

    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        self.scans.append(scan)
        return RecordOutcome.empty()

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        return RecordOutcome.empty()

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        return None

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        return 0

    async def get(self, session: AsyncSession, issue_id: str) -> IssueSnapshot | None:
        return self.snapshot if self.snapshot is not None and self.snapshot.id == issue_id else None

    async def open_resource_refs(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[OpenResourceRef, ...]:
        return ()


@dataclass
class CapturingResourceScanner:
    """Return a configured outcome and capture transient scan content."""

    outcome: RecordOutcome = RecordOutcome.empty()
    content: list[ResourceContent] = field(default_factory=list)
    scan_job_ids: list[str] = field(default_factory=list)

    async def scan_resource(
        self,
        content: ResourceContent,
        scan_job_id: str,
        detectors: tuple[DetectorKind, ...] | None = None,
    ) -> RecordOutcome:
        self.content.append(content)
        self.scan_job_ids.append(scan_job_id)
        return self.outcome


@dataclass
class PipelineAdapter:
    """Serve configured resources and shard entries for collector integration tests."""

    content: ResourceContent
    entries: tuple[ShardEntry, ...] = ()
    kind: CollectorKind = CollectorKind.SLACK

    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck:
        return EventCheck(EventCheckKind.VALID)

    async def resources_from_event(
        self, event: InboundWebhook, config: CollectorConfig
    ) -> EventResources:
        return EventResources((), ())

    async def list_changed(
        self, config: CollectorConfig, cursor: CollectorCursor | None
    ) -> ChangePage:
        return ChangePage((), (), cursor, False, False)

    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent:
        return self.content

    async def renew_watch(
        self, config: CollectorConfig, current: WatchState | None
    ) -> WatchState | None:
        return None

    def ref_from_json(self, ref_json: str) -> ResourceRef:
        return ResourceRef(self.kind, ref_json, self.content.raw_locator)

    async def partition(self, config: CollectorConfig, request: PartitionRequest) -> PartitionPage:
        return PartitionPage((), None, ())

    async def shard_resources(
        self, config: CollectorConfig, shard: ShardSpec
    ) -> AsyncIterator[ShardEntry]:
        for entry in self.entries:
            yield entry


@dataclass
class CapturingTaskQueue:
    """Provide an empty task queue for one-resource handler tests."""

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        return 0


@dataclass
class CapturingAudit:
    """Capture count-only audit events."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class CapturingNotifications:
    """Capture outbound requests without delivering them."""

    requests: list[NotificationRequest] = field(default_factory=list)

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        self.requests.append(request)
        return NotificationReceipt((), DlpVerdict.PASS, True)


def _config(
    config_id: str = "collector-1",
    cron: str | None = None,
    sweep_cron: str | None = None,
    trigger_mode: TriggerMode = TriggerMode.EVENT,
    created_at: datetime = NOW - timedelta(days=1),
) -> CollectorConfigRow:
    """Build a minimal persisted collector configuration."""
    return CollectorConfigRow(
        id=config_id,
        name="audit-drive",
        kind=CollectorKind.SLACK.value,
        trigger_mode=trigger_mode.value,
        cron=cron,
        sweep_cron=sweep_cron,
        sweep_max_concurrency=2,
        selector_json='{"channel_ids": [], "include_files": false}',
        credential_ref=None,
        cursor_json=None,
        enabled=True,
        created_at=created_at,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_coverage_records_counts_and_deletes_current_row() -> None:
    """Coverage aggregates counts while keeping only keyed resource identifiers."""
    harness: SqliteHarness = await make_sqlite_harness()
    recorder: CoverageRecorder = CoverageRecorder()
    content: ResourceContent = ResourceContent(
        "slack", "collector-1", (), False, False, "opaque", "hidden title"
    )
    try:
        async with harness.sessions.begin() as session:
            session.add(_config())
            session.add(
                ScanJobRow(
                    id="scan-1",
                    collector_config_id="collector-1",
                    source="slack",
                    trigger=ScanTrigger.EVENT.value,
                    status=ScanJobStatus.RUNNING.value,
                    started_at=NOW,
                )
            )
        async with harness.sessions.begin() as session:
            await recorder.record(
                session,
                "collector-1",
                "scan-1",
                "slack",
                "keyed-resource",
                Coverage(CoverageState.UNSUPPORTED, UnsupportedReason.IMAGE),
                Visibility.PUBLIC,
                NOW,
            )
            await recorder.record(
                session,
                "collector-1",
                "scan-1",
                "slack",
                "keyed-internal-resource",
                Coverage(CoverageState.PARTIAL),
                Visibility.INTERNAL,
                NOW + timedelta(seconds=1),
            )
        async with harness.sessions() as session:
            coverage_rows: list[ScanCoverageRow] = list(
                (await session.scalars(select(ScanCoverageRow))).all()
            )
            resource_rows: list[ResourceCoverageRow] = list(
                (await session.scalars(select(ResourceCoverageRow))).all()
            )
            summary: CoverageSummary = await recorder.summary(
                session, "collector-1", NOW - timedelta(hours=1)
            )
            public_page: tuple[
                tuple[CoverageResourceView, ...], str | None
            ] = await recorder.resources(session, "collector-1", None, 1, None)
            cursor: str | None = public_page[1]
            internal_page: tuple[
                tuple[CoverageResourceView, ...], str | None
            ] = await recorder.resources(session, "collector-1", None, 1, cursor)
            public_count: int = await recorder.public_unscannable_count(session, "collector-1")
        assert sorted((row.state, row.reason, row.count) for row in coverage_rows) == [
            ("partial", "", 1),
            ("unsupported", "image", 1),
        ]
        assert len(resource_rows) == 2
        assert cursor is not None and internal_page[1] is None
        assert public_page[0][0].resource_key == "keyed-resource"
        assert internal_page[0][0].resource_key == "keyed-internal-resource"
        assert public_count == 1
        assert summary.counts == (("partial", "", 1), ("unsupported", "image", 1))
        assert summary.unscannable_total == 2
        assert coverage_state(content, RecordOutcome.empty()).state is CoverageState.PARTIAL
        async with harness.sessions.begin() as session:
            await recorder.record(
                session,
                "collector-1",
                "scan-1",
                "slack",
                "keyed-resource",
                Coverage(CoverageState.SCANNED),
                Visibility.UNKNOWN,
                NOW + timedelta(minutes=1),
            )
        async with harness.sessions() as session:
            remaining: list[ResourceCoverageRow] = list(
                (await session.scalars(select(ResourceCoverageRow))).all()
            )
        assert len(remaining) == 1
        assert remaining[0].resource_key == "keyed-internal-resource"
        async with harness.sessions.begin() as session:
            await recorder.remove(session, "collector-1", "keyed-internal-resource")
    finally:
        await harness.close()


def test_coverage_state_prioritizes_skips_and_explicit_adapter_state() -> None:
    """Skipped resources become too_large, while adapter classifications survive."""
    scanned: ResourceContent = ResourceContent(
        "slack", "collector-1", (), True, True, "opaque", "title"
    )
    explicit: ResourceContent = ResourceContent(
        "slack",
        "collector-1",
        (),
        True,
        True,
        "opaque",
        "title",
        coverage=Coverage(CoverageState.UNSUPPORTED, UnsupportedReason.IMAGE),
    )
    skipped: RecordOutcome = RecordOutcome(0, 0, 0, 0, (), skipped=True)
    assert coverage_state(scanned, skipped).state is CoverageState.TOO_LARGE
    assert coverage_state(explicit, RecordOutcome.empty()).state is CoverageState.UNSUPPORTED


@pytest.mark.asyncio
async def test_scan_handler_records_removed_and_resource_coverage() -> None:
    """The task handler removes gone coverage and records fetched classifications."""
    harness: SqliteHarness = await make_sqlite_harness()
    repository: CollectorRepository = CollectorRepository()
    jobs: ScanJobStoreImpl = ScanJobStoreImpl(repository)
    content: ResourceContent = ResourceContent(
        "slack",
        "collector-1",
        (),
        False,
        True,
        "opaque-resource",
        "masked title",
        coverage=Coverage(CoverageState.UNSUPPORTED, UnsupportedReason.IMAGE),
    )
    adapter: PipelineAdapter = PipelineAdapter(content)
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(adapter)
    issues: CapturingIssueRecorder = CapturingIssueRecorder()
    scanner: CapturingResourceScanner = CapturingResourceScanner()
    fingerprinter: Fingerprinter = Fingerprinter(b"k" * 32, "test")
    coverage: CoverageRecorder = CoverageRecorder()
    handler: CollectorScanHandler = CollectorScanHandler(
        harness.sessions,
        registry,
        scanner,
        issues,
        issues,
        jobs,
        repository,
        CapturingTaskQueue(),
        fingerprinter,
        Redactor.default(),
        FakeClock(NOW),
        coverage=coverage,
    )
    resource_key: str = fingerprinter.resource_key("slack", content.raw_locator)
    try:
        async with harness.sessions.begin() as session:
            session.add(_config())
        async with harness.sessions.begin() as session:
            await coverage.record(
                session,
                "collector-1",
                None,
                "slack",
                resource_key,
                Coverage(CoverageState.PARTIAL),
                Visibility.INTERNAL,
                NOW,
            )
        removed_task: ClaimedTask = ClaimedTask(
            "task-removed",
            EngineTaskKind.COLLECTOR_SCAN,
            "collector-1",
            json_object(
                (
                    ("config_id", json_str("collector-1")),
                    ("mode", json_str("removed")),
                    ("ref_json", json_str('{"id":"removed"}')),
                )
            ),
            1,
            3,
            "removed-key",
        )
        removed_result: TaskResult = await handler.handle(removed_task)
        assert removed_result.kind.value == "done"
        assert issues.scans[0].removed is True
        assert issues.scans[0].complete is True
        async with harness.sessions() as session:
            removed_rows: list[ResourceCoverageRow] = list(
                (await session.scalars(select(ResourceCoverageRow))).all()
            )
        assert removed_rows == []

        resource_task: ClaimedTask = ClaimedTask(
            "task-resource",
            EngineTaskKind.COLLECTOR_SCAN,
            "collector-1",
            json_object(
                (
                    ("config_id", json_str("collector-1")),
                    ("mode", json_str("resource")),
                    ("ref_json", json_str('{"id":"resource"}')),
                )
            ),
            1,
            3,
            "resource-key",
        )
        resource_result: TaskResult = await handler.handle(resource_task)
        assert resource_result.kind.value == "done"
        scan_job_id: str = scanner.scan_job_ids[-1]
        async with harness.sessions() as session:
            job_rows: list[ScanCoverageRow] = list(
                (
                    await session.scalars(
                        select(ScanCoverageRow).where(ScanCoverageRow.scan_job_id == scan_job_id)
                    )
                ).all()
            )
            resource_rows: list[ResourceCoverageRow] = list(
                (await session.scalars(select(ResourceCoverageRow))).all()
            )
        assert [(row.state, row.reason, row.count) for row in job_rows] == [
            ("unsupported", "image", 1)
        ]
        assert len(resource_rows) == 1
        assert resource_rows[0].resource_key == resource_key
        assert resource_rows[0].state == "unsupported"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_sweeper_records_removed_and_forbidden_coverage() -> None:
    """Shard removal deletes coverage; forbidden failures remain unscannable."""
    harness: SqliteHarness = await make_sqlite_harness()
    repository: CollectorRepository = CollectorRepository()
    jobs: ScanJobStoreImpl = ScanJobStoreImpl(repository)
    removed_ref: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"removed"}', "removed")
    forbidden_ref: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"forbidden"}', "forbidden")
    content: ResourceContent = ResourceContent(
        "slack", "collector-1", (), True, True, "opaque-resource", "masked title"
    )
    adapter: PipelineAdapter = PipelineAdapter(
        content,
        (
            ShardEntry(ShardEntryKind.REMOVED, removed_ref),
            ShardEntry(ShardEntryKind.FAILED, forbidden_ref, error_code=ErrorCode.FORBIDDEN),
        ),
    )
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(adapter, adapter)
    issues: CapturingIssueRecorder = CapturingIssueRecorder()
    scanner: CapturingResourceScanner = CapturingResourceScanner()
    fingerprinter: Fingerprinter = Fingerprinter(b"k" * 32, "test")
    coverage: CoverageRecorder = CoverageRecorder()
    sweeper: CollectorSweeperImpl = CollectorSweeperImpl(
        harness.sessions,
        registry,
        repository,
        scanner,
        issues,
        jobs,
        fingerprinter,
        Redactor.default(),
        FakeClock(NOW),
        coverage,
    )
    removed_key: str = fingerprinter.resource_key("slack", removed_ref.raw_locator)
    forbidden_key: str = fingerprinter.resource_key("slack", forbidden_ref.raw_locator)
    try:
        async with harness.sessions.begin() as session:
            session.add(_config())
        async with harness.sessions.begin() as session:
            scan_job_id: str = await jobs.create(
                session, "collector-1", "slack", ScanTrigger.SCHEDULE, NOW
            )
            await coverage.record(
                session,
                "collector-1",
                None,
                "slack",
                removed_key,
                Coverage(CoverageState.PARTIAL),
                Visibility.INTERNAL,
                NOW,
            )
        summary: ShardSummary = await sweeper.scan_shard(
            "collector-1", ShardSpec("shard-1", json_object(()), 1), scan_job_id
        )
        assert summary.resources_failed == 1
        assert issues.scans[0].removed is True
        async with harness.sessions() as session:
            resource_rows: list[ResourceCoverageRow] = list(
                (await session.scalars(select(ResourceCoverageRow))).all()
            )
            count_rows: list[ScanCoverageRow] = list(
                (
                    await session.scalars(
                        select(ScanCoverageRow).where(ScanCoverageRow.scan_job_id == scan_job_id)
                    )
                ).all()
            )
        assert len(resource_rows) == 1
        assert resource_rows[0].resource_key == forbidden_key
        assert resource_rows[0].state == "permission_denied"
        assert [(row.state, row.reason, row.count) for row in count_rows] == [
            ("permission_denied", "", 1)
        ]
        assert removed_key not in {row.resource_key for row in resource_rows}
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_health_alerts_open_once_and_success_resets_failures() -> None:
    """Three failed jobs open one alert; another tick does not resend it."""
    harness: SqliteHarness = await make_sqlite_harness()
    recorder: CollectorHealthRecorder = CollectorHealthRecorder()
    notifications: CapturingNotifications = CapturingNotifications()
    audit: CapturingAudit = CapturingAudit()
    provider: CollectorHealthProvider = CollectorHealthProvider(
        CollectorRepository(), CoverageRecorder(), audit, notifications, ("team:secops",)
    )
    try:
        async with harness.sessions.begin() as session:
            session.add(_config())
            for _ in range(3):
                await recorder.job_finished(session, "collector-1", ScanJobStatus.FAILED, NOW)
            assert await provider.tick(session, NOW) == 1
        assert len(notifications.requests) == len(NotificationChannel)
        async with harness.sessions.begin() as session:
            assert await provider.tick(session, NOW + timedelta(seconds=30)) == 0
            assert await provider.tick(session, NOW + timedelta(minutes=2)) == 0
            assert len(notifications.requests) == len(NotificationChannel)
            assert len(audit.entries) == 1
            row: CollectorHealthRow | None = await session.get(CollectorHealthRow, "collector-1")
            assert row is not None
            assert row.consecutive_failures == 3
            await recorder.job_finished(
                session, "collector-1", ScanJobStatus.SUCCEEDED, NOW + timedelta(minutes=3)
            )
            await recorder.event_received(session, "collector-1", NOW + timedelta(minutes=4))
            await recorder.watch_started(
                session,
                "collector-1",
                NOW + timedelta(days=7),
                NOW + timedelta(minutes=4),
            )
        async with harness.sessions() as session:
            row: CollectorHealthRow | None = await session.get(CollectorHealthRow, "collector-1")
        assert row is not None
        assert row.consecutive_failures == 0
        assert row.last_success_at is not None
        assert row.last_event_at is not None
        assert row.watch_expires_at is not None
        assert ensure_utc(row.last_success_at) == NOW + timedelta(minutes=3)
        assert ensure_utc(row.last_event_at) == NOW + timedelta(minutes=4)
        assert ensure_utc(row.watch_expires_at) == NOW + timedelta(days=7)
        assert len(audit.entries) == 1
    finally:
        await harness.close()


def test_health_view_evaluates_stale_watch_public_and_event_only() -> None:
    """Health conditions use cron age, the twelve-hour watch window, and exposure counts."""
    stale_config: CollectorConfigRow = _config(
        cron="0 * * * *", trigger_mode=TriggerMode.BOTH, created_at=NOW - timedelta(hours=5)
    )
    row: CollectorHealthRow = CollectorHealthRow(
        collector_config_id="collector-1",
        last_success_at=NOW - timedelta(hours=3),
        last_failure_at=None,
        consecutive_failures=3,
        last_event_at=None,
        watch_expires_at=NOW + timedelta(hours=10),
        open_conditions="",
        alerted_at=None,
        updated_at=NOW,
    )
    view: CollectorHealthView = health_view(row, stale_config, 2, NOW)
    assert view.status is HealthStatus.FAILING
    assert view.conditions == (
        HealthCondition.STALE_SUCCESS,
        HealthCondition.WATCH_EXPIRING,
        HealthCondition.CONSECUTIVE_FAILURES,
        HealthCondition.UNSCANNABLE_PUBLIC,
    )
    suppressed: CollectorHealthView = health_view(
        row, stale_config, 2, NOW, alert_unscannable_public=False
    )
    assert HealthCondition.UNSCANNABLE_PUBLIC not in suppressed.conditions
    far_watch_row: CollectorHealthRow = CollectorHealthRow(
        collector_config_id="collector-1",
        last_success_at=NOW,
        last_failure_at=None,
        consecutive_failures=0,
        last_event_at=None,
        watch_expires_at=NOW + timedelta(hours=20),
        open_conditions="",
        alerted_at=None,
        updated_at=NOW,
    )
    far_watch: CollectorHealthView = health_view(far_watch_row, _config(), 0, NOW)
    assert HealthCondition.WATCH_EXPIRING not in far_watch.conditions
    event_only: CollectorHealthView = health_view(None, _config(), 0, NOW)
    assert event_only.event_only
    assert event_only.status is HealthStatus.WARNING
    scheduled_without_job: CollectorHealthView = health_view(
        None,
        _config(cron="0 * * * *", trigger_mode=TriggerMode.SCHEDULE, created_at=NOW),
        0,
        NOW,
    )
    assert scheduled_without_job.status is HealthStatus.UNKNOWN


@pytest.mark.asyncio
async def test_rescanner_marks_incomplete_and_gone_resources() -> None:
    """Incomplete scans are inconclusive; gone resources are removed complete scans."""
    from telescreen.ports.collectors import RescanKind

    harness: SqliteHarness = await make_sqlite_harness()
    repository: CollectorRepository = CollectorRepository()
    snapshot: IssueSnapshot = IssueSnapshot(
        "issue-1",
        1,
        "I-1",
        "masked title",
        IssueStatus.UNRESOLVED,
        "slack",
        RuleClass.CREDENTIAL,
        Severity.HIGH,
        "masked locator",
        1,
        NOW,
        NOW,
        "collector-1",
        '{"id":"safe"}',
    )
    reader: RescanReader = RescanReader(snapshot)
    content: ResourceContent = ResourceContent(
        "slack", "collector-1", (), False, True, "keyed-locator", "masked title"
    )
    adapter: RescanAdapter = RescanAdapter(content)
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(adapter)
    scanner: RescanScanner = RescanScanner()
    recorder: CoverageRecorder = CoverageRecorder()
    rescanner: CollectorRescanner = CollectorRescanner(
        harness.sessions,
        registry,
        reader,
        repository,
        scanner,
        ScanJobStoreImpl(repository),
        Fingerprinter(b"k" * 32, "test"),
        Redactor.default(),
        FakeClock(NOW),
        recorder,
    )
    try:
        async with harness.sessions.begin() as session:
            session.add(_config())
        incomplete: RescanResult = await rescanner.rescan("issue-1")
        assert incomplete.kind is RescanKind.INCONCLUSIVE
        assert incomplete.error_code is not None
        assert incomplete.error_code.value == "scan_incomplete"
        assert incomplete.coverage is CoverageState.PARTIAL
        async with harness.sessions() as session:
            resource_rows: list[ResourceCoverageRow] = list(
                (await session.scalars(select(ResourceCoverageRow))).all()
            )
        assert len(resource_rows) == 1
        scanner.outcome = RecordOutcome(1, 1, 0, 0, ("issue-1",))
        found: RescanResult = await rescanner.rescan("issue-1")
        assert found.kind is RescanKind.FOUND
        scanner.outcome = RecordOutcome.empty()
        adapter.gone = True
        gone: RescanResult = await rescanner.rescan("issue-1")
        assert gone.kind is RescanKind.CLEAN
        assert scanner.content[-1].removed
        assert scanner.content[-1].complete
        async with harness.sessions() as session:
            resource_rows = list((await session.scalars(select(ResourceCoverageRow))).all())
        assert resource_rows == []
    finally:
        await harness.close()
