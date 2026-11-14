"""Static structural checks for the public ports."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import (
    CollectorKind,
    DetectorKind,
    EngineTaskKind,
    ErrorCode,
    ScanJobStatus,
    ScanTrigger,
)
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.collectors import (
    ChangePage,
    CollectorAdapter,
    CollectorConfig,
    CollectorConfigReader,
    CollectorCursor,
    CollectorSecrets,
    EventCheck,
    EventResources,
    InboundWebhook,
    RescanResult,
    ResourceContent,
    ResourceRef,
    ResourceRescanner,
    ScanJobStore,
    WatchState,
)
from telescreen.ports.directory import AssigneeIds, RecipientResolver, ResolvedRecipients
from telescreen.ports.evidence import EvidenceQuery
from telescreen.ports.findings import ExternalScan, RecordOutcome, ResourceScan
from telescreen.ports.issues import (
    IssueEvent,
    IssueEventSink,
    IssueHistoryEntry,
    IssueReader,
    IssueRecorder,
    IssueSnapshot,
    OpenResourceRef,
    ResourceScanner,
)
from telescreen.ports.notifications import (
    NotificationReceipt,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.police import PoliceGateway, PoliceTaskRequest
from telescreen.ports.search import SearchIndexer
from telescreen.ports.sweeps import (
    CollectorSweeper,
    MapResultSink,
    PartitionPage,
    PartitionRequest,
    ShardEntry,
    ShardEntryKind,
    ShardFailure,
    ShardSpec,
    ShardSummary,
    SweepCapable,
    SweepLauncher,
)
from telescreen.ports.workflow import (
    ClaimedTask,
    RunSignaler,
    ScheduleProvider,
    StepSignal,
    TaskHandler,
    TaskQueue,
    TaskResult,
    TaskSpec,
)


class FakeAuditLog:
    """Structural fake for the audit append port."""

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        raise NotImplementedError


class FakeSearchIndexer:
    """Structural fake for post-commit indexing."""

    async def index_issue(self, issue_id: str) -> None:
        raise NotImplementedError

    async def index_finding(self, finding_id: str) -> None:
        raise NotImplementedError

    async def index_run(self, run_id: str) -> None:
        raise NotImplementedError


class FakeRecipientResolver:
    """Structural fake for directory recipient resolution."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        raise NotImplementedError

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        raise NotImplementedError

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        raise NotImplementedError


class FakeIssuePorts:
    """Structural fake for issue recorder, reader, and event sink ports."""

    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        raise NotImplementedError

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        raise NotImplementedError

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        raise NotImplementedError

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        raise NotImplementedError

    async def get(self, session: AsyncSession, issue_id: str) -> IssueSnapshot | None:
        raise NotImplementedError

    async def open_resource_refs(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[OpenResourceRef, ...]:
        raise NotImplementedError

    async def on_issue_event(self, session: AsyncSession, event: IssueEvent) -> None:
        raise NotImplementedError


class FakeResourceScanner:
    """Structural fake for transient resource scanning."""

    async def scan_resource(
        self,
        content: ResourceContent,
        scan_job_id: str,
        detectors: tuple[DetectorKind, ...] | None = None,
    ) -> RecordOutcome:
        raise NotImplementedError


@dataclass
class FakeCollectorPorts:
    """Structural fake for source adapters and collector configuration stores."""

    kind: CollectorKind = CollectorKind.CUSTOM

    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck:
        raise NotImplementedError

    async def resources_from_event(
        self, event: InboundWebhook, config: CollectorConfig
    ) -> EventResources:
        raise NotImplementedError

    async def list_changed(
        self, config: CollectorConfig, cursor: CollectorCursor | None
    ) -> ChangePage:
        raise NotImplementedError

    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent:
        raise NotImplementedError

    async def renew_watch(
        self, config: CollectorConfig, current: WatchState | None
    ) -> WatchState | None:
        raise NotImplementedError

    def ref_from_json(self, ref_json: str) -> ResourceRef:
        raise NotImplementedError

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        raise NotImplementedError

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        raise NotImplementedError

    async def create(
        self,
        session: AsyncSession,
        collector_config_id: str | None,
        source: str,
        trigger: ScanTrigger,
        started_at: datetime,
    ) -> str:
        raise NotImplementedError

    async def finish(
        self,
        session: AsyncSession,
        scan_job_id: str,
        status: ScanJobStatus,
        resources_scanned: int,
        resources_skipped: int,
        findings_count: int,
        error_code: ErrorCode | None,
        finished_at: datetime,
    ) -> None:
        raise NotImplementedError


class FakeResourceRescanner:
    """Structural fake for issue resource re-scans."""

    async def rescan(
        self, issue_id: str, detectors: tuple[DetectorKind, ...] | None = None
    ) -> RescanResult:
        raise NotImplementedError


class FakeSweepAdapter:
    """Structural fake for a source adapter's optional sweep capability."""

    async def partition(self, config: CollectorConfig, request: PartitionRequest) -> PartitionPage:
        raise NotImplementedError

    async def _entries(self) -> AsyncIterator[ShardEntry]:
        if False:
            yield ShardEntry(
                ShardEntryKind.REMOVED,
                ResourceRef(CollectorKind.CUSTOM, "{}", ""),
            )

    def shard_resources(
        self, config: CollectorConfig, shard: ShardSpec
    ) -> AsyncIterator[ShardEntry]:
        return self._entries()


class FakeCollectorSweeper:
    """Structural fake for collector-level sweep operations."""

    async def partition(self, collector_config_id: str, request: PartitionRequest) -> PartitionPage:
        raise NotImplementedError

    async def scan_shard(
        self, collector_config_id: str, shard: ShardSpec, scan_job_id: str
    ) -> ShardSummary:
        raise NotImplementedError


class FakeSweepLauncher:
    """Structural fake for collector sweep launch."""

    async def start_collector_sweep(self, collector_config_id: str, trigger: ScanTrigger) -> str:
        raise NotImplementedError


class FakeMapResultSink:
    """Structural fake for police map result persistence."""

    async def partition_page(
        self, session: AsyncSession, police_task_id: str, page: PartitionPage
    ) -> None:
        raise NotImplementedError

    async def shard_finished(
        self, session: AsyncSession, police_task_id: str, summary: ShardSummary
    ) -> None:
        raise NotImplementedError

    async def task_failed(
        self, session: AsyncSession, police_task_id: str, failure: ShardFailure
    ) -> None:
        raise NotImplementedError


class FakeNotificationSender:
    """Structural fake for notifications."""

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        raise NotImplementedError


class FakePoliceGateway:
    """Structural fake for transport-independent Police tasks."""

    async def dispatch(self, session: AsyncSession, request: PoliceTaskRequest) -> str:
        raise NotImplementedError

    async def cancel(self, session: AsyncSession, task_id: str) -> None:
        raise NotImplementedError


class FakeEvidenceQuery:
    """Structural fake for evidence gate queries."""

    async def accepted_count(self, session: AsyncSession, issue_id: str, step_run_id: str) -> int:
        raise NotImplementedError


@dataclass
class FakeWorkflowPorts:
    """Structural fake for workflow signals, queueing, handling, and scheduling."""

    kind: EngineTaskKind = EngineTaskKind.ADVANCE_STEP

    async def signal(self, session: AsyncSession, step_run_id: str, signal: StepSignal) -> None:
        raise NotImplementedError

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        raise NotImplementedError

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        raise NotImplementedError

    async def handle(self, task: ClaimedTask) -> TaskResult:
        raise NotImplementedError

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        raise NotImplementedError


def test_port_fakes_satisfy_protocols() -> None:
    """Type assignments verify each protocol's exact public signature."""
    audit_log: AuditLog = FakeAuditLog()
    search_indexer: SearchIndexer = FakeSearchIndexer()
    recipient_resolver: RecipientResolver = FakeRecipientResolver()
    issue_recorder: IssueRecorder = FakeIssuePorts()
    issue_reader: IssueReader = FakeIssuePorts()
    issue_event_sink: IssueEventSink = FakeIssuePorts()
    resource_scanner: ResourceScanner = FakeResourceScanner()
    resource_rescanner: ResourceRescanner = FakeResourceRescanner()
    collector_adapter: CollectorAdapter = FakeCollectorPorts()
    collector_config_reader: CollectorConfigReader = FakeCollectorPorts()
    scan_job_store: ScanJobStore = FakeCollectorPorts()
    sweep_capable: SweepCapable = FakeSweepAdapter()
    collector_sweeper: CollectorSweeper = FakeCollectorSweeper()
    sweep_launcher: SweepLauncher = FakeSweepLauncher()
    map_result_sink: MapResultSink = FakeMapResultSink()
    notification_sender: NotificationSender = FakeNotificationSender()
    police_gateway: PoliceGateway = FakePoliceGateway()
    evidence_query: EvidenceQuery = FakeEvidenceQuery()
    run_signaler: RunSignaler = FakeWorkflowPorts()
    task_queue: TaskQueue = FakeWorkflowPorts()
    task_handler: TaskHandler = FakeWorkflowPorts()
    schedule_provider: ScheduleProvider = FakeWorkflowPorts()
    assert all(
        (
            audit_log,
            search_indexer,
            recipient_resolver,
            issue_recorder,
            issue_reader,
            issue_event_sink,
            resource_scanner,
            resource_rescanner,
            collector_adapter,
            collector_config_reader,
            scan_job_store,
            sweep_capable,
            collector_sweeper,
            sweep_launcher,
            map_result_sink,
            notification_sender,
            police_gateway,
            evidence_query,
            run_signaler,
            task_queue,
            task_handler,
            schedule_provider,
        )
    )
