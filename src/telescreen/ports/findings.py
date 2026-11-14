"""Masked finding and scan DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from telescreen.core.vocabulary import DataAssetKind, DetectorKind, RuleClass, Severity
from telescreen.ports.collectors import Exposure
from telescreen.ports.inventory import ItemObservation


@dataclass(frozen=True)
class MaskedFinding:
    """A finding safe to persist after detection and masking."""

    rule_id: str
    rule_class: RuleClass
    severity: Severity
    detector: DetectorKind
    confidence: float
    masked_snippet: str
    location: str
    fingerprint: str
    fingerprint_key_id: str
    fingerprint_key_version: int = 1


@dataclass(frozen=True)
class ResourceScan:
    """A complete or partial scan result for one monitored resource."""

    source: str
    resource_key: str
    display_locator: str
    display_title: str
    collector_config_id: str | None
    scan_job_id: str | None
    ref_json: str | None
    findings: tuple[MaskedFinding, ...]
    complete: bool
    sweep_covered: bool
    observed_at: datetime
    exposure: Exposure | None = None
    owner_ref: str | None = None
    placement_keys: tuple[str, ...] = ()
    modified_at: datetime | None = None
    removed: bool = False
    items: ItemObservation | None = None
    asset_kind: DataAssetKind | None = None


@dataclass(frozen=True)
class LocatorInventory:
    """Safe item inventory reported for one external locator."""

    locator: str = field(repr=False)
    item_fingerprints: tuple[str, ...] | None = None
    item_count: int | None = None
    kind: DataAssetKind | None = None


@dataclass(frozen=True)
class LocatorExposure:
    """Exposure context associated with one external locator."""

    locator: str
    exposure: Exposure


@dataclass(frozen=True)
class ExternalFinding:
    """A finding submitted by an external source in masked form."""

    locator: str
    rule_id: str
    rule_class: RuleClass
    masked_snippet: str
    location: str
    confidence: float
    external_fingerprint: str


@dataclass(frozen=True)
class ExternalScan:
    """An externally submitted scan containing only masked findings."""

    source: str
    collector_config_id: str | None
    scan_job_id: str | None
    detector: DetectorKind
    findings: tuple[ExternalFinding, ...]
    complete_locators: tuple[str, ...]
    sweep_covered: bool
    observed_at: datetime
    exposures: tuple[LocatorExposure, ...] = ()
    inventories: tuple[LocatorInventory, ...] = ()


@dataclass(frozen=True)
class RecordOutcome:
    """Counts and issue identifiers produced while recording a scan."""

    findings_inserted: int
    issues_opened: int
    issues_regressed: int
    issues_auto_resolved: int
    issue_ids: tuple[str, ...]
    skipped: bool = False

    @staticmethod
    def empty() -> RecordOutcome:
        """Return an outcome containing no recorded findings or issues."""
        return RecordOutcome(0, 0, 0, 0, ())

    def plus(self, other: RecordOutcome) -> RecordOutcome:
        """Add counts and preserve issue ids in first-seen order."""
        issue_ids: list[str] = []
        for issue_id in self.issue_ids + other.issue_ids:
            if issue_id not in issue_ids:
                issue_ids.append(issue_id)
        return RecordOutcome(
            self.findings_inserted + other.findings_inserted,
            self.issues_opened + other.issues_opened,
            self.issues_regressed + other.issues_regressed,
            self.issues_auto_resolved + other.issues_auto_resolved,
            tuple(issue_ids),
            self.skipped or other.skipped,
        )
