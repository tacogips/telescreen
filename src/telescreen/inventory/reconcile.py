"""Reconcile inventory assets against authoritative collector results."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.vocabulary import DataAssetOrigin, DataAssetState, InventoryMode
from telescreen.db.models import DataAssetRow
from telescreen.inventory.lifecycle import delete_asset, stop_tracking_asset
from telescreen.inventory.repository import InventoryRepository
from telescreen.ports.collectors import CollectorConfig, CollectorConfigReader


@dataclass(frozen=True)
class InventoryReconcilerImpl:
    """Apply only authoritative sweep and full-listing absence signals."""

    configs: CollectorConfigReader
    repository: InventoryRepository
    clock: Clock

    async def reconcile_sweep(
        self, session: AsyncSession, collector_config_id: str, seen_before: datetime
    ) -> int:
        """Delete sweep-covered assets not observed after the sweep watermark."""
        if not await self._enabled(session, collector_config_id):
            return 0
        rows: tuple[DataAssetRow, ...] = await self._candidates(session, collector_config_id)
        removed: int = 0
        for asset in rows:
            if asset.last_seen_at is not None and ensure_utc(asset.last_seen_at) < ensure_utc(
                seen_before
            ):
                await delete_asset(
                    session,
                    asset,
                    self.repository,
                    f"collector:{collector_config_id}",
                    self.clock.now(),
                )
                removed += 1
        return removed

    async def reconcile_listing(
        self, session: AsyncSession, collector_config_id: str, listed_keys: frozenset[str]
    ) -> int:
        """Delete covered assets omitted from a complete resource listing."""
        if not await self._enabled(session, collector_config_id):
            return 0
        rows: tuple[DataAssetRow, ...] = await self._candidates(session, collector_config_id)
        removed: int = 0
        for asset in rows:
            if asset.resource_key not in listed_keys:
                await delete_asset(
                    session,
                    asset,
                    self.repository,
                    f"collector:{collector_config_id}",
                    self.clock.now(),
                )
                removed += 1
        return removed

    async def stop_tracking(
        self, session: AsyncSession, collector_config_id: str, actor_ref: str
    ) -> int:
        """Mark present discovered assets unknown when inventory tracking stops."""
        rows: tuple[DataAssetRow, ...] = tuple(
            (
                await session.scalars(
                    select(DataAssetRow)
                    .where(
                        DataAssetRow.collector_config_id == collector_config_id,
                        DataAssetRow.origin == DataAssetOrigin.DISCOVERED.value,
                        DataAssetRow.state == DataAssetState.PRESENT.value,
                    )
                    .with_for_update()
                )
            ).all()
        )
        for asset in rows:
            await stop_tracking_asset(session, asset, self.repository, actor_ref, self.clock.now())
        return len(rows)

    async def _enabled(self, session: AsyncSession, collector_config_id: str) -> bool:
        """Return whether the collector still contributes to inventory."""
        config: CollectorConfig | None = await self.configs.get(session, collector_config_id)
        return config is not None and config.inventory_mode is not InventoryMode.OFF

    async def _candidates(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[DataAssetRow, ...]:
        """Lock present, sweep-covered assets for one collector."""
        return tuple(
            (
                await session.scalars(
                    select(DataAssetRow)
                    .where(
                        DataAssetRow.collector_config_id == collector_config_id,
                        DataAssetRow.sweep_covered.is_(True),
                        DataAssetRow.state == DataAssetState.PRESENT.value,
                    )
                    .with_for_update()
                )
            ).all()
        )
