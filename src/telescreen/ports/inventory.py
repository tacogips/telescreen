"""Data inventory observation and reconciliation ports."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.ports.inventory_types import ItemObservation as ItemObservation
from telescreen.ports.inventory_types import ResourceObservation as ResourceObservation
from telescreen.ports.inventory_types import ValueFingerprint as ValueFingerprint


class InventoryObserver(Protocol):
    """Stage a safe observation in a producer-owned transaction.

    Runtime implementations write the local outbox; the receiver never sees this session."""

    async def observe(self, session: AsyncSession, observation: ResourceObservation) -> None: ...


class InventoryReconciler(Protocol):
    """Producer-local hooks for authoritative absence and tracking changes.

    Runtime publication returns zero immediate inventory mutations; delivery is asynchronous."""

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
