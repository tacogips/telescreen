"""Safe inventory observation values, independent of persistence interfaces."""

from dataclasses import dataclass
from datetime import datetime

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DataAssetKind, ErrorCode


@dataclass(frozen=True)
class ItemObservation:
    """Safe item-level fingerprints or a count for one observation."""

    fingerprints: tuple[str, ...] | None
    count: int | None

    def __post_init__(self) -> None:
        """Require exactly one item observation representation."""
        if (self.fingerprints is None) == (self.count is None):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid item observation")


@dataclass(frozen=True)
class ValueFingerprint:
    """Finding fingerprint and key version observed for a resource."""

    fingerprint: str
    key_version: int


@dataclass(frozen=True)
class ResourceObservation:
    """Safe resource metadata passed to the inventory observer."""

    source: str
    resource_key: str
    display_locator: str
    display_title: str
    collector_config_id: str | None
    scan_job_id: str | None
    observed_at: datetime
    complete: bool
    removed: bool
    sweep_covered: bool
    values: tuple[ValueFingerprint, ...]
    items: ItemObservation | None
    kind_hint: DataAssetKind | None
