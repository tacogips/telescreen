"""Transient resource scanning and issue persistence pipeline."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DetectorKind, ErrorCode
from telescreen.db.models import FindingRow
from telescreen.detection import DetectionResult, Detector, ScanItem
from telescreen.detection.types import check_results
from telescreen.issues.service import IssueService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.masking.seal import Masker
from telescreen.ports.collectors import ResourceContent
from telescreen.ports.findings import MaskedFinding, RecordOutcome, ResourceScan
from telescreen.ports.inventory import ItemObservation
from telescreen.ports.search import SearchIndexer


@dataclass
class ResourceScannerImpl:
    """Detect and persist masked findings without retaining source content."""

    sessions: async_sessionmaker[AsyncSession]
    detector: Detector
    detector_factory: Callable[[tuple[DetectorKind, ...]], Detector]
    masker: Masker
    fingerprinter: Fingerprinter
    redactor: Redactor
    recorder: IssueService
    indexer: SearchIndexer
    clock: Clock
    batch_items: int
    max_bytes: int
    _detectors: tuple[tuple[tuple[DetectorKind, ...], Detector], ...] = ()

    def __post_init__(self) -> None:
        """Reject invalid batch and size limits before processing content."""
        if self.batch_items < 1 or self.max_bytes < 1:
            raise TelescreenError(ErrorCode.CONFIG_ERROR, "invalid scan limits")
        self._detectors = ()

    async def scan_resource(
        self,
        content: ResourceContent,
        scan_job_id: str,
        detectors: tuple[DetectorKind, ...] | None = None,
    ) -> RecordOutcome:
        """Scan content transiently and return counts and issue identifiers."""
        total_bytes: int = sum(len(segment.text.encode("utf-8")) for segment in content.segments)
        observed_at: datetime = self.clock.now()
        if total_bytes > self.max_bytes:
            resource_scan: ResourceScan = self._resource_scan(
                content, scan_job_id, (), False, observed_at
            )
            async with self.sessions.begin() as session:
                outcome: RecordOutcome = await self.recorder.record_scan(session, resource_scan)
            return RecordOutcome(
                outcome.findings_inserted,
                outcome.issues_opened,
                outcome.issues_regressed,
                outcome.issues_auto_resolved,
                outcome.issue_ids,
                True,
            )

        items: list[ScanItem] = [
            ScanItem(f"{index}", content.source, segment.locator, segment.text)
            for index, segment in enumerate(content.segments)
        ]
        selected: Detector = self.detector if detectors is None else self._detector_for(detectors)
        sealed: list[MaskedFinding] = []
        for offset in range(0, len(items), self.batch_items):
            batch: list[ScanItem] = items[offset : offset + self.batch_items]
            results: list[DetectionResult] = await selected.detect_batch(batch)
            check_results(batch, results)
            for item, result in zip(batch, results, strict=True):
                sealed.extend(self.masker.seal(item, result))
        resource_scan = self._resource_scan(
            content, scan_job_id, tuple(sealed), content.complete, observed_at
        )
        async with self.sessions.begin() as session:
            outcome = await self.recorder.record_scan(session, resource_scan)
        for issue_id in outcome.issue_ids:
            await self.indexer.index_issue(issue_id)
        if outcome.issue_ids:
            async with self.sessions() as session:
                new_rows: list[FindingRow] = list(
                    (
                        await session.scalars(
                            select(FindingRow).where(
                                FindingRow.scan_job_id == scan_job_id,
                                FindingRow.issue_id.in_(outcome.issue_ids),
                            )
                        )
                    ).all()
                )
                finding_ids: tuple[str, ...] = tuple(row.id for row in new_rows)
            for finding_id in finding_ids:
                await self.indexer.index_finding(finding_id)
        return outcome

    def _detector_for(self, kinds: tuple[DetectorKind, ...]) -> Detector:
        """Return a cached detector pipeline for explicit detector kinds."""
        for key, detector in self._detectors:
            if key == kinds:
                return detector
        detector = self.detector_factory(kinds)
        self._detectors += ((kinds, detector),)
        return detector

    def _resource_scan(
        self,
        content: ResourceContent,
        scan_job_id: str,
        findings: tuple[MaskedFinding, ...],
        complete: bool,
        observed_at: datetime,
    ) -> ResourceScan:
        """Build a safe persistence DTO from transient content."""
        raw_locator: str = content.raw_locator
        reference_json: str | None = content.ref.ref_json if content.ref is not None else None
        if reference_json is not None and self.redactor.has_hits(reference_json):
            reference_json = None
        items: ItemObservation | None = None
        if content.items is not None:
            if content.items.keys is not None:
                fingerprints: tuple[str, ...] = tuple(
                    sorted(
                        {
                            self.fingerprinter.item_key(
                                self.fingerprinter.resource_key(content.source, raw_locator), key
                            )
                            for key in content.items.keys
                        }
                    )
                )
                items = ItemObservation(fingerprints, None)
            elif content.items.count is not None:
                items = ItemObservation(None, content.items.count)
        return ResourceScan(
            content.source,
            self.fingerprinter.resource_key(content.source, raw_locator),
            self.redactor.redact(raw_locator).text,
            self.redactor.redact(content.display_title).text[:512],
            content.collector_config_id,
            scan_job_id,
            reference_json,
            findings,
            complete and (content.coverage is None or content.coverage.state.value == "scanned"),
            content.sweep_covered,
            observed_at,
            exposure=content.exposure,
            owner_ref=(
                self.fingerprinter.owner_ref(content.owner) if content.owner is not None else None
            ),
            placement_keys=tuple(
                self.fingerprinter.placement_key(item.kind, item.value)
                for item in content.placement
            ),
            modified_at=content.modified_at,
            removed=content.removed,
            items=items,
            asset_kind=content.asset_kind,
        )
