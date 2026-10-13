"""Data inventory observation and reconciliation ports."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

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


class InventoryObserver(Protocol):
    """Record one observed data resource in the caller's transaction."""

    async def observe(self, session: AsyncSession, observation: ResourceObservation) -> None: ...


class InventoryReconciler(Protocol):
    """Reconcile assets absent from an authoritative scan or listing."""

    async def reconcile_sweep(
        self, session: AsyncSession, collector_config_id: str, seen_before: datetime
    ) -> int: ...

    async def reconcile_listing(
        self, session: AsyncSession, collector_config_id: str, listed_keys: frozenset[str]
    ) -> int: ...

    async def stop_tracking(
        self, session: AsyncSession, collector_config_id: str, actor_ref: str
    ) -> int: ...


class AssetOwnerResolver(Protocol):
    """Resolve a fallback owner reference for an asset."""

    async def fallback_owner(
        self, session: AsyncSession, source: str, resource_key: str, display_locator: str
    ) -> str | None: ...


@dataclass(frozen=True)
class NoopInventoryObserver:
    """Observer implementation for callers without inventory wiring."""

    async def observe(self, session: AsyncSession, observation: ResourceObservation) -> None:
        """Ignore an observation when inventory is disabled."""
        del session, observation


@dataclass(frozen=True)
class NoopInventoryReconciler:
    """Reconciler implementation for callers without inventory wiring."""

    async def reconcile_sweep(
        self, session: AsyncSession, collector_config_id: str, seen_before: datetime
    ) -> int:
        """Report no changes for a sweep."""
        del session, collector_config_id, seen_before
        return 0

    async def reconcile_listing(
        self, session: AsyncSession, collector_config_id: str, listed_keys: frozenset[str]
    ) -> int:
        """Report no changes for a listing."""
        del session, collector_config_id, listed_keys
        return 0

    async def stop_tracking(
        self, session: AsyncSession, collector_config_id: str, actor_ref: str
    ) -> int:
        """Report no changes when tracking stops."""
        del session, collector_config_id, actor_ref
        return 0
