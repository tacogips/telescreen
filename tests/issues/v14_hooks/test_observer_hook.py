"""Inventory observer behavior at the issue record boundary."""

from dataclasses import dataclass, replace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import DataAssetKind, DetectorKind, RuleClass
from telescreen.db.models import IssueRow
from telescreen.detection import Detector, RuleDetector
from telescreen.issues.scan import ResourceScannerImpl
from telescreen.masking.seal import Masker
from telescreen.ports.collectors import ItemReport, ResourceContent, Segment
from telescreen.ports.findings import (
    ExternalFinding,
    ExternalScan,
    LocatorInventory,
    MaskedFinding,
    ResourceScan,
)
from telescreen.ports.inventory import ItemObservation, ResourceObservation
from tests.issues.test_issues import (
    IssueHarness,
    RecordingIndexer,
    make_issue_harness,
    masked_finding,
    resource_scan,
)


@dataclass
class RecordingInventory:
    """Capture inventory calls made inside the issue transaction."""

    observations: list[ResourceObservation]

    async def observe(self, session: AsyncSession, observation: ResourceObservation) -> None:
        """Record the safe DTO and leave transaction ownership to the caller."""
        del session
        self.observations.append(observation)


@dataclass
class FailingInventory:
    """Fail the caller transaction after issue processing."""

    async def observe(self, session: AsyncSession, observation: ResourceObservation) -> None:
        """Raise to prove the inventory hook participates in the caller transaction."""
        del session, observation
        raise RuntimeError("observer failed")


@pytest.mark.asyncio
async def test_record_scan_observes_after_issue_processing_with_distinct_values() -> None:
    """The hook sees a complete scan and unique finding fingerprints."""
    harness: IssueHarness = await make_issue_harness()
    try:
        observer: RecordingInventory = RecordingInventory([])
        harness.service.inventory = observer
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "same-value")
        scan: ResourceScan = resource_scan(harness, (finding, finding), "scan-1")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, scan)
        assert len(observer.observations) == 1
        assert observer.observations[0].complete
        assert not observer.observations[0].removed
        assert len(observer.observations[0].values) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_external_inventory_is_rekeyed_per_resource() -> None:
    """External item fingerprints are re-keyed before observer delivery."""
    harness: IssueHarness = await make_issue_harness()
    try:
        observer: RecordingInventory = RecordingInventory([])
        harness.service.inventory = observer
        external_fingerprint: str = "a" * 64
        scan: ExternalScan = ExternalScan(
            "custom:test",
            "collector-1",
            None,
            DetectorKind.POLICE,
            (
                ExternalFinding(
                    "resource:2", "test.email", RuleClass.EMAIL, "masked", "cell", 0.9, "b" * 64
                ),
            ),
            ("resource:3",),
            False,
            harness.clock.now(),
            inventories=(
                LocatorInventory("resource:2", (external_fingerprint,), None, DataAssetKind.LIST),
            ),
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_external(session, scan)
        assert len(observer.observations) == 2
        inventory_observation: ResourceObservation = next(
            observation for observation in observer.observations if observation.items is not None
        )
        items: ItemObservation | None = inventory_observation.items
        assert items is not None and items.fingerprints is not None
        assert items.fingerprints[0] != external_fingerprint
        assert inventory_observation.kind_hint is DataAssetKind.LIST
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_removed_scan_is_observed() -> None:
    """A removed empty scan reaches the observer with its definitive signal."""
    harness: IssueHarness = await make_issue_harness()
    try:
        observer: RecordingInventory = RecordingInventory([])
        harness.service.inventory = observer
        scan: ResourceScan = resource_scan(harness, (), "scan-removed")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, replace(scan, removed=True))
        assert observer.observations[0].removed
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_observer_failure_rolls_back_issue_transaction() -> None:
    """An observer error rolls back issue rows created earlier in the same call."""
    harness: IssueHarness = await make_issue_harness()
    try:
        harness.service.inventory = FailingInventory()
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "rollback-value")
        with pytest.raises(RuntimeError, match="observer failed"):
            async with harness.db.sessions.begin() as session:
                await harness.service.record_scan(
                    session, resource_scan(harness, (finding,), "scan-fail")
                )
        async with harness.db.sessions() as session:
            issues: list[IssueRow] = list((await session.scalars(select(IssueRow))).all())
        assert not issues
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_scanner_keys_raw_item_keys_before_building_resource_scan() -> None:
    """Raw item keys become per-resource HMAC fingerprints before persistence DTOs."""
    harness: IssueHarness = await make_issue_harness()
    try:
        detector: Detector = RuleDetector()

        def detector_factory(kinds: tuple[DetectorKind, ...]) -> Detector:
            del kinds
            return detector

        scanner: ResourceScannerImpl = ResourceScannerImpl(
            harness.db.sessions,
            detector,
            detector_factory,
            Masker(harness.fingerprinter, harness.redactor),
            harness.fingerprinter,
            harness.redactor,
            harness.service,
            RecordingIndexer([], []),
            harness.clock,
            4,
            1000,
        )
        content: ResourceContent = ResourceContent(
            "custom:test",
            "collector-1",
            (),
            False,
            False,
            "resource:3",
            "Resource",
            items=ItemReport(keys=("key-a", "key-a", "key-b")),
        )
        scan: ResourceScan = scanner._resource_scan(
            content, "scan-2", (), False, harness.clock.now()
        )
        assert scan.items is not None and scan.items.fingerprints is not None
        expected: tuple[str, ...] = tuple(
            sorted(
                {
                    harness.fingerprinter.item_key(scan.resource_key, key)
                    for key in ("key-a", "key-b")
                }
            )
        )
        assert scan.items.fingerprints == expected
        assert all("key-a" not in fingerprint for fingerprint in scan.items.fingerprints)
        observer: RecordingInventory = RecordingInventory([])
        harness.service.inventory = observer
        scanner.max_bytes = 1
        oversized: ResourceContent = ResourceContent(
            "custom:test",
            "collector-1",
            (Segment("part", "too large"),),
            True,
            False,
            "resource:4",
            "Resource",
            items=ItemReport(keys=("key-c",)),
        )
        await scanner.scan_resource(oversized, "scan-too-large")
        assert len(observer.observations) == 1
        assert not observer.observations[0].complete
        assert observer.observations[0].items is not None
    finally:
        await harness.close()
