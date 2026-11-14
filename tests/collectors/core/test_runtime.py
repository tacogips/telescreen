"""Behavioral tests for collector task, scheduling, Police, rescan, and sweep paths."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.collectors.codec import CursorState, cursor_from_json
from telescreen.collectors.police import PoliceCollector
from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.repository import CollectorRepository, ScanJobStoreImpl
from telescreen.collectors.rescanner import CollectorRescanner
from telescreen.collectors.scheduler import CollectorScheduleProvider
from telescreen.collectors.sweep import CollectorSweeperImpl
from telescreen.collectors.tasks import CollectorScanHandler
from telescreen.core.clock import FakeClock
from telescreen.core.json import JsonValue, dump_json, json_object, json_str
from telescreen.core.vocabulary import (
    CollectorKind,
    DetectorKind,
    EngineTaskKind,
    ErrorCode,
    IssueStatus,
    RuleClass,
    ScanTrigger,
    Severity,
    TriggerMode,
)
from telescreen.db.models import CollectorConfigRow
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    EventCheck,
    EventCheckKind,
    EventResources,
    InboundWebhook,
    PoliceSelector,
    RateLimited,
    RescanResult,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    SlackSelector,
    WatchState,
)
from telescreen.ports.findings import ExternalScan, RecordOutcome, ResourceScan
from telescreen.ports.issues import IssueHistoryEntry, IssueSnapshot, OpenResourceRef
from telescreen.ports.police import PoliceTaskRequest
from telescreen.ports.sweeps import (
    PartitionPage,
    PartitionRequest,
    ShardEntry,
    ShardEntryKind,
    ShardSpec,
    ShardSummary,
)
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind, TaskSpec
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import assert_no_leaks, email_address

NOW: datetime = datetime(2026, 10, 9, 12, tzinfo=UTC)
RAW_EMAIL: str = email_address().value


@dataclass
class RuntimeAdapter:
    """Configurable adapter and shard fake for collector runtime tests."""

    kind: CollectorKind = CollectorKind.SLACK
    pages: tuple[ChangePage, ...] = ()
    event_resources: EventResources = EventResources((), ())
    gone: bool = False
    rate_limited: bool = False
    list_calls: int = 0
    fetched: list[ResourceRef] = field(default_factory=list)
    entries: tuple[ShardEntry, ...] = ()

    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck:
        return EventCheck(EventCheckKind.VALID)

    async def resources_from_event(
        self, event: InboundWebhook, config: CollectorConfig
    ) -> EventResources:
        return self.event_resources

    async def list_changed(
        self, config: CollectorConfig, cursor: CollectorCursor | None
    ) -> ChangePage:
        if self.rate_limited:
            raise RateLimited(5)
        page: ChangePage = self.pages[self.list_calls]
        self.list_calls += 1
        return page

    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent:
        if self.gone:
            raise ResourceGoneError()
        self.fetched.append(ref)
        return ResourceContent(
            config.kind.value, config.id, (), True, True, ref.raw_locator, "safe title", ref
        )

    async def renew_watch(self, config: CollectorConfig, current: WatchState | None) -> WatchState:
        return WatchState("channel-1", "resource-1", NOW + timedelta(days=7))

    def ref_from_json(self, ref_json: str) -> ResourceRef:
        locator: str = (
            RAW_EMAIL if "covered" in ref_json else "listed" if "listed" in ref_json else "outside"
        )
        return ResourceRef(self.kind, ref_json, locator)

    async def partition(self, config: CollectorConfig, request: PartitionRequest) -> PartitionPage:
        return PartitionPage((), None, ())

    async def shard_resources(
        self, config: CollectorConfig, shard: ShardSpec
    ) -> AsyncIterator[ShardEntry]:
        for entry in self.entries:
            yield entry


@dataclass
class RuntimePorts:
    """Capture durable side effects without persisting source content."""

    tasks: list[TaskSpec] = field(default_factory=list)
    scans: list[ResourceScan] = field(default_factory=list)
    snapshot: IssueSnapshot | None = None
    open_refs: tuple[OpenResourceRef, ...] = ()
    scan_outcome: RecordOutcome = RecordOutcome.empty()
    police_requests: list[PoliceTaskRequest] = field(default_factory=list)
    sweep_launches: list[tuple[str, ScanTrigger]] = field(default_factory=list)

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        if any(task.idempotency_key == spec.idempotency_key for task in self.tasks):
            return False
        self.tasks.append(spec)
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        return 0

    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        self.scans.append(scan)
        return self.scan_outcome

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        return self.scan_outcome

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        return None

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        return 0

    async def get(self, session: AsyncSession, issue_id: str) -> IssueSnapshot | None:
        return self.snapshot

    async def open_resource_refs(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[OpenResourceRef, ...]:
        return self.open_refs

    async def dispatch(self, session: AsyncSession, request: PoliceTaskRequest) -> str:
        self.police_requests.append(request)
        return "police-task-1"

    async def cancel(self, session: AsyncSession, task_id: str) -> None:
        return None

    async def start_collector_sweep(self, collector_config_id: str, trigger: ScanTrigger) -> str:
        self.sweep_launches.append((collector_config_id, trigger))
        return "map-job-1"


@dataclass
class RuntimeScanner:
    """Capture the transient content and explicit detector selection."""

    ports: RuntimePorts
    outcome: RecordOutcome = RecordOutcome.empty()
    outcomes: tuple[RecordOutcome, ...] = ()
    observed: list[ResourceContent] = field(default_factory=list)
    detectors: tuple[DetectorKind, ...] | None = None

    async def scan_resource(
        self,
        content: ResourceContent,
        scan_job_id: str,
        detectors: tuple[DetectorKind, ...] | None = None,
    ) -> RecordOutcome:
        self.observed.append(content)
        self.detectors = detectors
        if self.outcomes:
            return self.outcomes[len(self.observed) - 1]
        return self.outcome


@pytest.mark.asyncio
async def test_scan_handler_advances_pages_and_retries_rate_limit() -> None:
    """Changes scans advance cursors and upstream backoff becomes a timed retry."""
    harness: SqliteHarness = await make_sqlite_harness()
    repo: CollectorRepository = CollectorRepository()
    jobs: ScanJobStoreImpl = ScanJobStoreImpl(repo)
    ports: RuntimePorts = RuntimePorts()
    ref: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"safe"}', RAW_EMAIL)
    adapter: RuntimeAdapter = RuntimeAdapter(
        pages=(
            ChangePage((ref,), (), CollectorCursor("cursor-1"), False, True),
            ChangePage((), (ref,), CollectorCursor("cursor-2"), False, False),
        )
    )
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(adapter)
    scanner: RuntimeScanner = RuntimeScanner(ports)
    try:
        config: CollectorConfig = await _create_config(harness, repo, NOW)
        handler: CollectorScanHandler = _scan_handler(harness, registry, scanner, ports, jobs, repo)
        task: ClaimedTask = _scan_task(config.id, "changes", None)
        result: TaskResult = await handler.handle(task)
        assert result.kind is TaskResultKind.DONE
        assert adapter.list_calls == 2
        assert len(scanner.observed) == 1
        assert len(ports.scans) == 1
        async with harness.sessions() as session:
            row_state: CollectorConfigRow | None = await session.get(CollectorConfigRow, config.id)
            assert row_state is not None
            assert cursor_from_json(row_state.cursor_json).cursor == "cursor-2"

        adapter.rate_limited = True
        retry: TaskResult = await handler.handle(task)
        assert retry.kind is TaskResultKind.RETRY
        assert retry.available_at == NOW + timedelta(seconds=5)
        assert retry.error_code is ErrorCode.RATE_LIMITED
        assert_no_leaks(dump_json(task.payload), "collector task payload")
        assert_no_leaks(repr(ports.scans), "recorded scans")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_scan_modes_record_gone_and_reconcile_only_covered_resources() -> None:
    """Gone resources resolve cleanly and full listings spare uncovered refs."""
    harness: SqliteHarness = await make_sqlite_harness()
    repo: CollectorRepository = CollectorRepository()
    jobs: ScanJobStoreImpl = ScanJobStoreImpl(repo)
    ports: RuntimePorts = RuntimePorts()
    listed: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"listed"}', "listed")
    covered: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"covered"}', RAW_EMAIL)
    outside: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"outside"}', "outside")
    adapter: RuntimeAdapter = RuntimeAdapter(
        gone=True,
        pages=(
            ChangePage((listed,), (), CollectorCursor("page-1"), True, True),
            ChangePage((), (), CollectorCursor("page-2"), True, False),
        ),
    )
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(adapter)
    scanner: RuntimeScanner = RuntimeScanner(ports)
    try:
        config: CollectorConfig = await _create_config(harness, repo, NOW)
        handler: CollectorScanHandler = _scan_handler(harness, registry, scanner, ports, jobs, repo)
        ports.open_refs = (
            OpenResourceRef(
                "issue-covered",
                Fingerprinter(b"k" * 32, "test").resource_key("slack", covered.raw_locator),
                covered.ref_json,
                True,
            ),
            OpenResourceRef(
                "issue-outside",
                Fingerprinter(b"k" * 32, "test").resource_key("slack", outside.raw_locator),
                outside.ref_json,
                False,
            ),
        )
        resource_result: TaskResult = await handler.handle(
            _scan_task(config.id, "resource", listed.ref_json)
        )
        removed_result: TaskResult = await handler.handle(
            _scan_task(config.id, "removed", outside.ref_json)
        )
        changes_result: TaskResult = await handler.handle(_scan_task(config.id, "changes", None))
        assert resource_result.kind is TaskResultKind.DONE
        assert removed_result.kind is TaskResultKind.DONE
        assert changes_result.kind is TaskResultKind.DONE
        assert len(ports.scans) == 4
        assert all(scan.complete for scan in ports.scans)
        expected_key: str = Fingerprinter(b"k" * 32, "test").resource_key(
            "slack", covered.raw_locator
        )
        assert ports.scans[-1].resource_key == expected_key
        assert all("outside" not in scan.resource_key for scan in ports.scans)
        assert_no_leaks(repr(ports.scans), "removed and reconciled scans")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_scheduler_is_idempotent_and_renews_expiring_watch() -> None:
    """A due schedule enqueues once and a near-expiry watch queues renewal."""
    harness: SqliteHarness = await make_sqlite_harness()
    repo: CollectorRepository = CollectorRepository()
    ports: RuntimePorts = RuntimePorts()
    police: PoliceCollector = PoliceCollector(ports, FakeClock(NOW))
    provider: CollectorScheduleProvider = CollectorScheduleProvider(
        ports, ports, police, repo, FakeClock(NOW)
    )
    try:
        config: CollectorConfig = await _create_config(
            harness,
            repo,
            NOW,
            mode=TriggerMode.BOTH,
            cron="0 12 * * *",
            sweep_cron="0 12 * * *",
        )
        async with harness.sessions.begin() as session:
            await repo.set_cursor(
                session,
                config.id,
                CursorState(None, WatchState("channel", "resource", NOW + timedelta(hours=4))),
                NOW,
            )
            assert await provider.tick(session, NOW) == 3
        async with harness.sessions.begin() as session:
            assert await provider.tick(session, NOW) == 0
        assert len(ports.tasks) == 2
        assert len(ports.sweep_launches) == 1
        assert {task.kind for task in ports.tasks} == {
            EngineTaskKind.COLLECTOR_SCAN,
            EngineTaskKind.COLLECTOR_WATCH_RENEW,
        }
        assert_no_leaks(repr(ports.tasks), "scheduled task payloads")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_police_dispatch_and_rescan_gone_preserve_resource_identity() -> None:
    """Police dispatch carries labels and a gone rescan retains the ref identity."""
    harness: SqliteHarness = await make_sqlite_harness()
    repo: CollectorRepository = CollectorRepository()
    ports: RuntimePorts = RuntimePorts()
    adapter: RuntimeAdapter = RuntimeAdapter(gone=True)
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(adapter)
    scanner: RuntimeScanner = RuntimeScanner(ports)
    try:
        config: CollectorConfig = await _create_config(harness, repo, NOW)
        police_config: CollectorConfig = CollectorConfig(
            "police-config",
            "police-check",
            CollectorKind.POLICE,
            TriggerMode.EVENT,
            None,
            None,
            16,
            PoliceSelector(("database",), json_object((("scope", json_str("private")),))),
            None,
            True,
        )
        async with harness.sessions() as session:
            assert (
                await PoliceCollector(ports, FakeClock(NOW)).dispatch_detect(session, police_config)
                == "police-task-1"
            )
        request: PoliceTaskRequest = ports.police_requests[0]
        assert request.task_type == "detect" and request.labels == ("database",)
        assert request.collector_config_id == "police-config"
        ports.snapshot = IssueSnapshot(
            "issue-1",
            1,
            "I-1",
            "safe title",
            IssueStatus.UNRESOLVED,
            "slack",
            RuleClass.CREDENTIAL,
            Severity.HIGH,
            "masked",
            1,
            NOW,
            NOW,
            config.id,
            '{"id":"covered"}',
        )
        rescanner: CollectorRescanner = CollectorRescanner(
            harness.sessions,
            registry,
            ports,
            repo,
            scanner,
            ScanJobStoreImpl(repo),
            Fingerprinter(b"k" * 32, "test"),
            Redactor.default(),
            FakeClock(NOW),
        )
        result: RescanResult = await rescanner.rescan("issue-1", (DetectorKind.RULE,))
        assert result.kind.value == "clean"
        assert scanner.observed[-1].raw_locator == RAW_EMAIL
        assert scanner.observed[-1].ref is not None
        assert scanner.detectors == (DetectorKind.RULE,)
        ports.snapshot = IssueSnapshot(
            "issue-1",
            1,
            "I-1",
            "safe title",
            IssueStatus.UNRESOLVED,
            "slack",
            RuleClass.CREDENTIAL,
            Severity.HIGH,
            "masked",
            1,
            NOW,
            NOW,
            config.id,
            None,
        )
        no_ref: RescanResult = await rescanner.rescan("issue-1")
        assert no_ref.kind.value == "error"
        assert no_ref.error_code is ErrorCode.NOT_RESCANNABLE
        ports.snapshot = IssueSnapshot(
            "issue-1",
            1,
            "I-1",
            "safe title",
            IssueStatus.UNRESOLVED,
            "slack",
            RuleClass.CREDENTIAL,
            Severity.HIGH,
            "masked",
            1,
            NOW,
            NOW,
            config.id,
            '{"id":"covered"}',
        )
        adapter.gone = False
        scanner.outcome = RecordOutcome(1, 1, 0, 0, ("issue-1",))
        found: RescanResult = await rescanner.rescan("issue-1")
        assert found.kind.value == "found"
        assert_no_leaks(repr(request), "Police dispatch request")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_sweeper_counts_fetched_removed_and_failed_resources() -> None:
    """Shard processing records removal and incomplete failures with count-only totals."""
    harness: SqliteHarness = await make_sqlite_harness()
    repo: CollectorRepository = CollectorRepository()
    ports: RuntimePorts = RuntimePorts()
    ref_fetched: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"one"}', RAW_EMAIL)
    ref_removed: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"two"}', "gone")
    ref_failed: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"three"}', "failed")
    content: ResourceContent = ResourceContent(
        "slack", "cfg-1", (), True, True, RAW_EMAIL, "safe title", ref_fetched
    )
    adapter: RuntimeAdapter = RuntimeAdapter(
        entries=(
            ShardEntry(ShardEntryKind.FETCHED, ref_fetched, content),
            ShardEntry(
                ShardEntryKind.FETCHED,
                ref_fetched,
                ResourceContent(
                    "slack", "cfg-1", (), True, True, "second", "safe title", ref_fetched
                ),
            ),
            ShardEntry(ShardEntryKind.REMOVED, ref_removed),
            ShardEntry(ShardEntryKind.FAILED, ref_failed, error_code=ErrorCode.FETCH_FAILED),
        )
    )
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(adapter, adapter)
    scanner: RuntimeScanner = RuntimeScanner(
        ports,
        outcomes=(
            RecordOutcome(1, 1, 0, 0, ("issue-1",)),
            RecordOutcome(0, 0, 0, 0, (), skipped=True),
        ),
    )
    try:
        await _create_config(harness, repo, NOW)
        async with harness.sessions.begin() as session:
            job_id: str = await repo.create_scan_job(
                session, "cfg-1", "slack", ScanTrigger.SCHEDULE, NOW
            )
        sweeper: CollectorSweeperImpl = CollectorSweeperImpl(
            harness.sessions,
            registry,
            repo,
            scanner,
            ports,
            ScanJobStoreImpl(repo),
            Fingerprinter(b"k" * 32, "test"),
            Redactor.default(),
            FakeClock(NOW),
        )
        summary: ShardSummary = await sweeper.scan_shard(
            "cfg-1", ShardSpec("shard-1", json_object(()), 1), job_id
        )
        assert summary.resources_scanned == 1
        assert summary.resources_skipped == 1
        assert summary.resources_failed == 1
        assert len(ports.scans) == 2
        assert ports.scans[0].complete is True
        assert ports.scans[1].complete is False
        assert_no_leaks(repr(ports.scans), "sweep scan records")
    finally:
        await harness.close()


async def _create_config(
    harness: SqliteHarness,
    repo: CollectorRepository,
    now: datetime,
    mode: TriggerMode = TriggerMode.EVENT,
    cron: str | None = None,
    sweep_cron: str | None = None,
) -> CollectorConfig:
    async with harness.sessions.begin() as session:
        return await repo.create(
            session,
            "cfg-1",
            "slack-prod",
            CollectorKind.SLACK,
            mode,
            cron,
            sweep_cron,
            16,
            SlackSelector(("C123",), False),
            None,
            now,
        )


def _scan_handler(
    harness: SqliteHarness,
    registry: CollectorRegistry,
    scanner: RuntimeScanner,
    ports: RuntimePorts,
    jobs: ScanJobStoreImpl,
    repo: CollectorRepository,
) -> CollectorScanHandler:
    return CollectorScanHandler(
        harness.sessions,
        registry,
        scanner,
        ports,
        ports,
        jobs,
        repo,
        Fingerprinter(b"k" * 32, "test"),
        Redactor.default(),
        FakeClock(NOW),
    )


def _scan_task(config_id: str, mode: str, ref_json: str | None) -> ClaimedTask:
    payload_fields: list[tuple[str, JsonValue]] = [
        ("config_id", json_str(config_id)),
        ("mode", json_str(mode)),
    ]
    if ref_json is not None:
        payload_fields.append(("ref_json", json_str(ref_json)))
    payload: JsonValue = json_object(tuple(payload_fields))
    return ClaimedTask(
        "task-1", EngineTaskKind.COLLECTOR_SCAN, config_id, payload, 1, 3, "idempotency-1"
    )
