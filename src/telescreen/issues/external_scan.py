"""External finding validation and recording for issue scans."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, severity_for
from telescreen.ports.findings import (
    ExternalScan,
    LocatorInventory,
    MaskedFinding,
    RecordOutcome,
    ResourceScan,
)
from telescreen.ports.inventory import ItemObservation

if TYPE_CHECKING:
    from telescreen.issues.service import IssueService


async def record_external_scan(
    service: IssueService, session: AsyncSession, scan: ExternalScan
) -> RecordOutcome:
    """Validate and re-key externally supplied findings before recording."""
    if any(service.redactor.has_hits(locator) for locator in scan.complete_locators):
        raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "raw sensitive value detected")
    for finding in scan.findings:
        values: tuple[str, ...] = (
            finding.locator,
            finding.rule_id,
            finding.rule_class.value,
            finding.masked_snippet,
            finding.location,
            finding.external_fingerprint,
        )
        if any(service.redactor.has_hits(value) for value in values):
            raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "raw sensitive value detected")
    by_locator: list[tuple[str, list[MaskedFinding]]] = []
    for finding in scan.findings:
        raw_locator: str = f"{scan.source}:{finding.locator}"
        masked: MaskedFinding = MaskedFinding(
            finding.rule_id,
            finding.rule_class,
            severity_for(finding.rule_class),
            scan.detector,
            finding.confidence,
            finding.masked_snippet,
            service.redactor.redact(finding.location).text,
            service.fingerprinter.external(scan.source, finding.external_fingerprint),
            service.fingerprinter.key_id,
            fingerprint_key_version=service.fingerprinter.key_version,
        )
        group: list[MaskedFinding] | None = next(
            (values for locator, values in by_locator if locator == raw_locator), None
        )
        if group is None:
            group = []
            by_locator.append((raw_locator, group))
        group.append(masked)
    outcomes: RecordOutcome = RecordOutcome.empty()
    locators: list[str] = [locator for locator, _ in by_locator]
    for locator in scan.complete_locators:
        complete_locator: str = f"{scan.source}:{locator}"
        if complete_locator not in locators:
            locators.append(complete_locator)
    for inventory in scan.inventories:
        inventory_locator: str = f"{scan.source}:{inventory.locator}"
        if inventory_locator not in locators:
            locators.append(inventory_locator)
    for raw_locator in sorted(locators):
        findings: tuple[MaskedFinding, ...] = tuple(
            finding
            for locator, values in by_locator
            if locator == raw_locator
            for finding in values
        )
        local_locator: str = raw_locator[len(scan.source) + 1 :]
        resource_key: str = service.fingerprinter.resource_key(scan.source, raw_locator)
        inventory: LocatorInventory | None = next(
            (entry for entry in scan.inventories if entry.locator == local_locator), None
        )
        items: ItemObservation | None = None
        if inventory is not None:
            fingerprints: tuple[str, ...] | None = (
                tuple(
                    sorted(
                        {
                            service.fingerprinter.item_key(resource_key, fingerprint)
                            for fingerprint in inventory.item_fingerprints
                        }
                    )
                )
                if inventory.item_fingerprints is not None
                else None
            )
            if fingerprints is not None:
                items = ItemObservation(fingerprints, None)
            elif inventory.item_count is not None:
                items = ItemObservation(None, inventory.item_count)
        resource_scan: ResourceScan = ResourceScan(
            scan.source,
            resource_key,
            service.redactor.redact(raw_locator).text,
            service.redactor.redact(local_locator).text,
            scan.collector_config_id,
            scan.scan_job_id,
            None,
            findings,
            local_locator in scan.complete_locators,
            scan.sweep_covered,
            scan.observed_at,
            exposure=next(
                (entry.exposure for entry in scan.exposures if entry.locator == local_locator),
                None,
            ),
            items=items,
            asset_kind=inventory.kind if inventory is not None else None,
        )
        outcomes = outcomes.plus(await service.record_scan(session, resource_scan))
    return outcomes
