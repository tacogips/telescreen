"""Typed in-memory implementations of Police service ports for offline tests."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonValue
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import CollectorConfig
from telescreen.ports.findings import ExternalScan, RecordOutcome, ResourceScan
from telescreen.ports.issues import IssueHistoryEntry
from telescreen.ports.sweeps import MapResultSink, PartitionPage, ShardFailure, ShardSummary
from telescreen.ports.workflow import RunSignaler, StepSignal, TaskSpec


@dataclass
class RecordingAudit:
    """Capture audit metadata for assertions without external systems."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record the audit event in memory."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class FakeConfigs:
    """Return no collector configuration for generic Police tasks."""

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        """No collector rows are needed in these focused tests."""
        del session, collector_config_id
        return None

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        """No collector rows are needed in these focused tests."""
        del session, name
        return None


@dataclass
class FakeIssues:
    """Capture external scans sent by Police results."""

    scans: list[ExternalScan] = field(default_factory=list)

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        """Record the submitted scan DTO and return empty counts."""
        del session
        self.scans.append(scan)
        return RecordOutcome.empty()

    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        """Satisfy the broader IssueRecorder protocol for unrelated tests."""
        del session, scan
        return RecordOutcome.empty()

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        """Ignore unrelated issue history in these focused tests."""
        del session, entry

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        """Return no reconciled issues in focused Police tests."""
        del session, collector_config_id, checked_before
        return 0


@dataclass
class FakeSignaler(RunSignaler):
    """Capture workflow signals from accepted Police results."""

    signals: list[tuple[str, StepSignal]] = field(default_factory=list)

    async def signal(self, session: AsyncSession, step_run_id: str, signal: StepSignal) -> None:
        """Record a safe workflow result signal."""
        del session
        self.signals.append((step_run_id, signal))


@dataclass
class FakeMapSink(MapResultSink):
    """Capture sweep result and failure routing."""

    failures: list[tuple[str, ShardFailure]] = field(default_factory=list)
    summaries: list[tuple[str, ShardSummary]] = field(default_factory=list)
    pages: list[tuple[str, PartitionPage]] = field(default_factory=list)

    async def partition_page(
        self, session: AsyncSession, police_task_id: str, page: PartitionPage
    ) -> None:
        """Record a partition page."""
        del session
        self.pages.append((police_task_id, page))

    async def shard_finished(
        self, session: AsyncSession, police_task_id: str, summary: ShardSummary
    ) -> None:
        """Record count-only shard completion."""
        del session
        self.summaries.append((police_task_id, summary))

    async def task_failed(
        self, session: AsyncSession, police_task_id: str, failure: ShardFailure
    ) -> None:
        """Record a safe shard failure."""
        del session
        self.failures.append((police_task_id, failure))


@dataclass
class FakeQueue:
    """Record durable queue requests for gateway tests."""

    specs: list[JsonValue] = field(default_factory=list)

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Capture the task payload as JSON-like test metadata."""
        del session
        self.specs.append(spec.payload)
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Report no existing tasks canceled."""
        del session, ref_id
        return 0
