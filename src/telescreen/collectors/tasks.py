"""Durable collector scan and watch-renewal task handlers."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.collectors.codec import CursorState, cursor_from_json
from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.repository import CollectorRepository
from telescreen.collectors.service import CollectorService
from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import EngineTaskKind, ErrorCode, ScanJobStatus, ScanTrigger
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    RateLimited,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    ScanJobStore,
)
from telescreen.ports.findings import RecordOutcome, ResourceScan
from telescreen.ports.issues import IssueReader, IssueRecorder, OpenResourceRef, ResourceScanner
from telescreen.ports.workflow import ClaimedTask, TaskHandler, TaskResult


@dataclass(frozen=True)
class CollectorScanHandler(TaskHandler):
    """Run resource, removal, or incremental collector scans from durable tasks."""

    sessions: async_sessionmaker[AsyncSession]
    registry: CollectorRegistry
    scanner: ResourceScanner
    issues: IssueRecorder
    reader: IssueReader
    scan_jobs: ScanJobStore
    repository: CollectorRepository
    fingerprinter: Fingerprinter
    redactor: Redactor
    clock: Clock
    max_pages: int = 20
    kind: EngineTaskKind = EngineTaskKind.COLLECTOR_SCAN

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Handle one collector scan and map transient failures to stable retries."""
        config_id_value: JsonValue | None = task.payload.get("config_id")
        mode_value: JsonValue | None = task.payload.get("mode")
        config_id: str | None = config_id_value.as_str() if config_id_value is not None else None
        mode: str | None = mode_value.as_str() if mode_value is not None else None
        if config_id is None or mode not in ("resource", "removed", "changes"):
            return TaskResult.dead(ErrorCode.VALIDATION_ERROR)
        async with self.sessions() as session:
            config: CollectorConfig | None = await self.repository.get(session, config_id)
            row_json: str | None = await _cursor_json(session, config_id)
        if config is None:
            return TaskResult.dead(ErrorCode.NOT_FOUND)
        async with self.sessions.begin() as session:
            job_id: str = await self.scan_jobs.create(
                session, config.id, config.kind.value, ScanTrigger.EVENT, self.clock.now()
            )
        try:
            if mode == "resource" or mode == "removed":
                ref_value: JsonValue | None = task.payload.get("ref_json")
                ref_json: str | None = ref_value.as_str() if ref_value is not None else None
                if ref_json is None or self.redactor.has_hits(ref_json):
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid resource reference")
                ref: ResourceRef = self.registry.adapter(config.kind).ref_from_json(ref_json)
                if mode == "removed":
                    outcome: RecordOutcome = await self._record_empty(config, ref, job_id, True)
                else:
                    try:
                        content: ResourceContent = await self.registry.adapter(config.kind).fetch(
                            config, ref
                        )
                        outcome = await self.scanner.scan_resource(content, job_id)
                    except ResourceGoneError:
                        outcome = await self._record_empty(config, ref, job_id, True)
                await self._finish(
                    job_id,
                    ScanJobStatus.SUCCEEDED,
                    1,
                    int(outcome.skipped),
                    outcome.findings_inserted,
                    None,
                )
                return TaskResult.done()
            await self._changes(config, row_json, job_id)
            return TaskResult.done()
        except RateLimited as limited:
            delay: float = (
                limited.retry_after_seconds if limited.retry_after_seconds is not None else 5.0
            )
            await self._finish(job_id, ScanJobStatus.PARTIAL, 0, 0, 0, ErrorCode.RATE_LIMITED)
            return TaskResult.retry(
                self.clock.now() + timedelta(seconds=delay), ErrorCode.RATE_LIMITED
            )
        except ResourceGoneError:
            await self._finish(job_id, ScanJobStatus.PARTIAL, 0, 0, 0, ErrorCode.FETCH_FAILED)
            return TaskResult.retry(self.clock.now() + timedelta(seconds=5), ErrorCode.FETCH_FAILED)
        except TelescreenError as error:
            await self._finish(job_id, ScanJobStatus.FAILED, 0, 0, 0, error.code)
            return TaskResult.dead(error.code)
        except Exception:
            await self._finish(job_id, ScanJobStatus.PARTIAL, 0, 0, 0, ErrorCode.FETCH_FAILED)
            return TaskResult.retry(self.clock.now() + timedelta(seconds=5), ErrorCode.FETCH_FAILED)

    async def _changes(self, config: CollectorConfig, stored_json: str | None, job_id: str) -> None:
        """Consume incremental pages, advancing cursors and reconciling complete listings."""
        state: CursorState = cursor_from_json(stored_json)
        cursor: CollectorCursor | None = (
            CollectorCursor(state.cursor) if state.cursor is not None else None
        )
        listed: set[str] = set()
        pages: int = 0
        scanned: int = 0
        skipped: int = 0
        findings: int = 0
        full_listing: bool = False
        while True:
            page: ChangePage = await self.registry.adapter(config.kind).list_changed(config, cursor)
            pages += 1
            full_listing = full_listing or page.full_listing
            for ref in page.changed:
                if self.redactor.has_hits(ref.ref_json):
                    skipped += 1
                    continue
                resource_key: str = self.fingerprinter.resource_key(
                    config.kind.value, ref.raw_locator
                )
                listed.add(resource_key)
                try:
                    content: ResourceContent = await self.registry.adapter(config.kind).fetch(
                        config, ref
                    )
                    outcome: RecordOutcome = await self.scanner.scan_resource(content, job_id)
                    findings += outcome.findings_inserted
                    skipped += int(outcome.skipped)
                    scanned += 1
                except ResourceGoneError:
                    await self._record_empty(config, ref, job_id, True)
                    scanned += 1
            for ref in page.removed:
                await self._record_empty(config, ref, job_id, True)
                scanned += 1
            cursor = page.next_cursor
            state = CursorState(
                cursor.value if cursor is not None else state.cursor,
                state.watch,
                state.last_sweep_fire,
            )
            async with self.sessions.begin() as session:
                await self.repository.set_cursor(session, config.id, state, self.clock.now())
            if not page.has_more or (not full_listing and pages >= self.max_pages):
                break
        if full_listing:
            async with self.sessions() as session:
                open_refs: tuple[OpenResourceRef, ...] = await self.reader.open_resource_refs(
                    session, config.id
                )
            for open_ref in open_refs:
                if (
                    open_ref.sweep_covered
                    and open_ref.resource_key not in listed
                    and open_ref.ref_json is not None
                ):
                    empty_ref: ResourceRef = self.registry.adapter(config.kind).ref_from_json(
                        open_ref.ref_json
                    )
                    await self._record_empty(config, empty_ref, job_id, True)
        await self._finish(job_id, ScanJobStatus.SUCCEEDED, scanned, skipped, findings, None)

    async def _record_empty(
        self, config: CollectorConfig, ref: ResourceRef, job_id: str, complete: bool
    ) -> RecordOutcome:
        """Record a content-free complete or incomplete scan for a reference."""
        display: str = self.redactor.redact(ref.raw_locator).text
        scan: ResourceScan = ResourceScan(
            config.kind.value,
            self.fingerprinter.resource_key(config.kind.value, ref.raw_locator),
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
            return await self.issues.record_scan(session, scan)

    async def _finish(
        self,
        job_id: str,
        status: ScanJobStatus,
        scanned: int,
        skipped: int,
        findings: int,
        error: ErrorCode | None,
    ) -> None:
        """Persist count-only task outcome."""
        async with self.sessions.begin() as session:
            await self.scan_jobs.finish(
                session, job_id, status, scanned, skipped, findings, error, self.clock.now()
            )


@dataclass(frozen=True)
class WatchRenewHandler(TaskHandler):
    """Renew a collector's upstream watch from a durable task."""

    sessions: async_sessionmaker[AsyncSession]
    service: CollectorService
    kind: EngineTaskKind = EngineTaskKind.COLLECTOR_WATCH_RENEW

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Renew one watch and return a safe task result."""
        try:
            async with self.sessions.begin() as session:
                await self.service.start_watch(session, task.ref_id)
            return TaskResult.done()
        except TelescreenError as error:
            return TaskResult.dead(error.code)


async def _cursor_json(session: AsyncSession, config_id: str) -> str | None:
    """Read a persisted collector cursor."""
    from telescreen.db.models import CollectorConfigRow

    row: CollectorConfigRow | None = await session.get(CollectorConfigRow, config_id)
    return row.cursor_json if row is not None else None
