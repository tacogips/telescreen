"""Collector implementation of the map-reduce sweep port."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.repository import CollectorRepository, ScanJobStoreImpl
from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, ScanJobStatus
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import CollectorConfig, RateLimited, ResourceContent, ResourceRef
from telescreen.ports.findings import RecordOutcome, ResourceScan
from telescreen.ports.issues import IssueRecorder, ResourceScanner
from telescreen.ports.sweeps import (
    CollectorSweeper,
    PartitionPage,
    PartitionRequest,
    ShardEntry,
    ShardEntryKind,
    ShardSpec,
    ShardSummary,
    SweepCapable,
)


@dataclass(frozen=True)
class CollectorSweeperImpl(CollectorSweeper):
    """Resolve collectors and scan each item in a map shard."""

    sessions: async_sessionmaker[AsyncSession]
    registry: CollectorRegistry
    configs: CollectorRepository
    scanner: ResourceScanner
    issues: IssueRecorder
    scan_jobs: ScanJobStoreImpl
    fingerprinter: Fingerprinter
    redactor: Redactor
    clock: Clock

    async def partition(self, collector_config_id: str, request: PartitionRequest) -> PartitionPage:
        """Delegate partitioning to a source adapter."""
        config: CollectorConfig = await self._config(collector_config_id)
        sweeper: SweepCapable | None = self.registry.sweeper(config.kind)
        if sweeper is None:
            raise TelescreenError(ErrorCode.SWEEP_UNSUPPORTED, "collector does not support sweeps")
        return await sweeper.partition(config, request)

    async def scan_shard(
        self, collector_config_id: str, shard: ShardSpec, scan_job_id: str
    ) -> ShardSummary:
        """Scan all shard resources and return count-only results."""
        config: CollectorConfig = await self._config(collector_config_id)
        sweeper: SweepCapable | None = self.registry.sweeper(config.kind)
        if sweeper is None:
            raise TelescreenError(ErrorCode.SWEEP_UNSUPPORTED, "collector does not support sweeps")
        scanned: int = 0
        skipped: int = 0
        failed: int = 0
        byte_count: int = 0
        findings: int = 0
        opened: int = 0
        resolved: int = 0
        entries: AsyncIterator[ShardEntry] = sweeper.shard_resources(config, shard)
        async for entry in entries:
            if entry.kind is ShardEntryKind.FETCHED and entry.content is not None:
                try:
                    outcome: RecordOutcome = await self.scanner.scan_resource(
                        entry.content, scan_job_id
                    )
                    byte_count += sum(
                        len(segment.text.encode("utf-8")) for segment in entry.content.segments
                    )
                    findings += outcome.findings_inserted
                    opened += outcome.issues_opened
                    resolved += outcome.issues_auto_resolved
                    if outcome.skipped:
                        skipped += 1
                    else:
                        scanned += 1
                except RateLimited:
                    raise
                except Exception:
                    failed += 1
                    await self._record_entry_failure(config, entry, scan_job_id)
            elif entry.kind is ShardEntryKind.REMOVED:
                await self._record_empty(config, entry.content, entry.ref, scan_job_id, True)
            else:
                failed += 1
                await self._record_empty(config, entry.content, entry.ref, scan_job_id, False)
        async with self.sessions.begin() as session:
            await self.scan_jobs.finish(
                session,
                scan_job_id,
                ScanJobStatus.PARTIAL if failed else ScanJobStatus.SUCCEEDED,
                scanned,
                skipped,
                findings,
                ErrorCode.FETCH_FAILED if failed else None,
                self.clock.now(),
            )
        return ShardSummary(scanned, skipped, failed, byte_count, findings, opened, resolved, ())

    async def _config(self, config_id: str) -> CollectorConfig:
        """Load one collector config."""
        async with self.sessions() as session:
            config: CollectorConfig | None = await self.configs.get(session, config_id)
        if config is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
        return config

    async def _record_entry_failure(
        self, config: CollectorConfig, entry: ShardEntry, job_id: str
    ) -> None:
        """Record a failed resource as incomplete without resolving issues."""
        await self._record_empty(config, entry.content, entry.ref, job_id, False)

    async def _record_empty(
        self,
        config: CollectorConfig,
        content: ResourceContent | None,
        ref: ResourceRef,
        job_id: str,
        complete: bool,
    ) -> None:
        """Record a removed or failed item with no raw content persisted."""
        raw_locator: str = content.raw_locator if content is not None else ref.raw_locator
        display: str = self.redactor.redact(raw_locator).text
        scan: ResourceScan = ResourceScan(
            config.kind.value,
            self.fingerprinter.resource_key(config.kind.value, raw_locator),
            display,
            "",
            config.id,
            job_id,
            ref.ref_json,
            (),
            complete,
            True,
            self.clock.now(),
        )
        async with self.sessions.begin() as session:
            await self.issues.record_scan(session, scan)
