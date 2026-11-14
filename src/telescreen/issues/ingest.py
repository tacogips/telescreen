"""Use cases for custom collector push ingestion."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    CollectorKind,
    DetectorKind,
    ErrorCode,
    ScanJobStatus,
    ScanTrigger,
)
from telescreen.issues.service import IssueService
from telescreen.ports.collectors import (
    CollectorConfig,
    CollectorConfigReader,
    ResourceContent,
    ScanJobStore,
)
from telescreen.ports.findings import ExternalFinding, ExternalScan, RecordOutcome
from telescreen.ports.issues import ResourceScanner
from telescreen.ports.search import SearchIndexer


@dataclass(frozen=True)
class IngestScanResult:
    """Safe aggregate result for one pushed scan job."""

    scan_job_id: str
    resources: int
    findings: int
    issue_ids: tuple[str, ...]


@dataclass
class IngestService:
    """Validate push sources and route content through the scan pipeline."""

    sessions: async_sessionmaker[AsyncSession]
    scanner: ResourceScanner
    recorder: IssueService
    configs: CollectorConfigReader
    scan_jobs: ScanJobStore
    indexer: SearchIndexer
    clock: Clock

    async def scan_push(
        self, source: str, resources: Sequence[ResourceContent]
    ) -> IngestScanResult:
        """Create a push scan job and scan each resource without retaining content."""
        collector_name: str = self._source_name(source)
        async with self.sessions.begin() as session:
            config: CollectorConfig | None = await self.configs.get_by_name(session, collector_name)
            if config is None or config.kind is not CollectorKind.CUSTOM or not config.enabled:
                raise TelescreenError(ErrorCode.UNKNOWN_SOURCE, "unknown ingestion source")
            scan_job_id: str = await self.scan_jobs.create(
                session, config.id, source, ScanTrigger.PUSH, self.clock.now()
            )
        outcome: RecordOutcome = RecordOutcome.empty()
        skipped: int = 0
        for content in resources:
            normalized: ResourceContent = ResourceContent(
                source,
                config.id,
                content.segments,
                content.complete,
                False,
                content.raw_locator,
                content.display_title,
                content.ref,
            )
            resource_outcome: RecordOutcome = await self.scanner.scan_resource(
                normalized, scan_job_id
            )
            outcome = outcome.plus(resource_outcome)
            if resource_outcome.skipped:
                skipped += 1
        async with self.sessions.begin() as session:
            await self.scan_jobs.finish(
                session,
                scan_job_id,
                ScanJobStatus.SUCCEEDED,
                len(resources),
                skipped,
                outcome.findings_inserted,
                None,
                self.clock.now(),
            )
        return IngestScanResult(
            scan_job_id, len(resources), outcome.findings_inserted, outcome.issue_ids
        )

    async def findings_push(
        self, source: str, locator: str, complete: bool, findings: Sequence[ExternalFinding]
    ) -> RecordOutcome:
        """Validate a custom source and record only re-keyed masked findings."""
        collector_name: str = self._source_name(source)
        async with self.sessions.begin() as session:
            config: CollectorConfig | None = await self.configs.get_by_name(session, collector_name)
            if config is None or config.kind is not CollectorKind.CUSTOM or not config.enabled:
                raise TelescreenError(ErrorCode.UNKNOWN_SOURCE, "unknown ingestion source")
            return await self.recorder.record_external(
                session,
                ExternalScan(
                    source,
                    config.id,
                    None,
                    DetectorKind.POLICE,
                    tuple(findings),
                    (locator,) if complete else (),
                    False,
                    self.clock.now(),
                ),
            )

    @staticmethod
    def _source_name(source: str) -> str:
        """Extract the configured custom source name without echoing input."""
        prefix: str = "custom:"
        name: str = source[len(prefix) :] if source.startswith(prefix) else ""
        if not name or len(name) > 128:
            raise TelescreenError(ErrorCode.UNKNOWN_SOURCE, "unknown ingestion source")
        return name
