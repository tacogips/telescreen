"""Workflow-triggered collector resource rescans."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.repository import CollectorRepository, ScanJobStoreImpl
from telescreen.core.clock import Clock
from telescreen.core.vocabulary import DetectorKind, ErrorCode, ScanJobStatus, ScanTrigger
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import (
    CollectorAdapter,
    CollectorConfig,
    RescanKind,
    RescanResult,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
)
from telescreen.ports.findings import RecordOutcome
from telescreen.ports.issues import IssueReader, IssueSnapshot, ResourceScanner


@dataclass(frozen=True)
class CollectorRescanner:
    """Resolve and scan the source resource associated with an issue."""

    sessions: async_sessionmaker[AsyncSession]
    registry: CollectorRegistry
    reader: IssueReader
    configs: CollectorRepository
    scanner: ResourceScanner
    scan_jobs: ScanJobStoreImpl
    fingerprinter: Fingerprinter
    redactor: Redactor
    clock: Clock

    async def rescan(
        self, issue_id: str, detectors: tuple[DetectorKind, ...] | None = None
    ) -> RescanResult:
        """Scan the issue's resource and report only found/clean/error state."""
        async with self.sessions() as session:
            snapshot: IssueSnapshot | None = await self.reader.get(session, issue_id)
        if snapshot is None or snapshot.ref_json is None or snapshot.collector_config_id is None:
            return RescanResult(RescanKind.ERROR, ErrorCode.NOT_RESCANNABLE)
        async with self.sessions() as session:
            config: CollectorConfig | None = await self.configs.get(
                session, snapshot.collector_config_id
            )
        if config is None:
            return RescanResult(RescanKind.ERROR, ErrorCode.NOT_RESCANNABLE)
        adapter: CollectorAdapter = self.registry.adapter(config.kind)
        try:
            ref: ResourceRef = adapter.ref_from_json(snapshot.ref_json)
        except Exception:
            return RescanResult(RescanKind.ERROR, ErrorCode.FETCH_FAILED)
        try:
            content: ResourceContent = await adapter.fetch(config, ref)
        except ResourceGoneError:
            content = ResourceContent(
                snapshot.source,
                snapshot.collector_config_id,
                (),
                True,
                True,
                ref.raw_locator,
                snapshot.display_title,
                ref,
            )
        except Exception:
            return RescanResult(RescanKind.ERROR, ErrorCode.FETCH_FAILED)
        async with self.sessions.begin() as session:
            job_id: str = await self.scan_jobs.create(
                session, config.id, snapshot.source, ScanTrigger.WORKFLOW, self.clock.now()
            )
        try:
            outcome: RecordOutcome = await self.scanner.scan_resource(content, job_id, detectors)
        except Exception:
            async with self.sessions.begin() as session:
                await self.scan_jobs.finish(
                    session,
                    job_id,
                    ScanJobStatus.FAILED,
                    0,
                    0,
                    0,
                    ErrorCode.FETCH_FAILED,
                    self.clock.now(),
                )
            return RescanResult(RescanKind.ERROR, ErrorCode.FETCH_FAILED)
        async with self.sessions.begin() as session:
            await self.scan_jobs.finish(
                session,
                job_id,
                ScanJobStatus.SUCCEEDED,
                1,
                int(outcome.skipped),
                outcome.findings_inserted,
                None,
                self.clock.now(),
            )
        kind: RescanKind = RescanKind.FOUND if issue_id in outcome.issue_ids else RescanKind.CLEAN
        return RescanResult(kind)
