"""Observe collector resources and persist safe inventory lifecycle changes."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.ids import new_id
from telescreen.core.vocabulary import (
    DataAssetKind,
    DataAssetOrigin,
    DataAssetState,
    DataEventKind,
    DataItemKind,
    InventoryMode,
)
from telescreen.db.models import DataAssetRow
from telescreen.inventory.items import (
    CountDelta,
    ItemDelta,
    apply_count,
    apply_fingerprints,
    apply_values,
)
from telescreen.inventory.lifecycle import delete_asset, restore_asset, resume_asset
from telescreen.inventory.repository import DataEventDraft, InventoryRepository
from telescreen.ports.inventory_types import ItemObservation, ResourceObservation


@dataclass(frozen=True)
class InventoryObserverImpl:
    """Persist resource observations in the caller's transaction."""

    repository: InventoryRepository

    async def apply_observation(
        self, session: AsyncSession, observation: ResourceObservation, mode: InventoryMode
    ) -> None:
        """Apply an observation using captured policy, without consulting collector tables."""
        if mode is InventoryMode.OFF:
            return
        asset: DataAssetRow | None = await self.repository.get_by_resource_key(
            session, observation.resource_key, lock=True
        )
        if asset is not None and asset.origin == DataAssetOrigin.REGISTERED.value:
            return
        if asset is not None and _is_stale(asset, observation):
            return
        actor_ref: str = (
            f"collector:{observation.collector_config_id}"
            if observation.collector_config_id is not None
            else "system:inventory"
        )
        if observation.removed:
            if asset is not None and asset.state != DataAssetState.DELETED.value:
                await delete_asset(
                    session,
                    asset,
                    self.repository,
                    actor_ref,
                    observation.observed_at,
                    observation.scan_job_id,
                )
            return
        values: frozenset[tuple[str, int]] = frozenset(
            (value.fingerprint, value.key_version) for value in observation.values
        )
        if asset is None:
            if mode is InventoryMode.SENSITIVE_ONLY and not values:
                return
            created_asset: DataAssetRow
            created: bool
            created_asset, created = await self._create(session, observation)
            asset = created_asset
            if created:
                await self._record_created(session, asset, observation, actor_ref, values)
                return
        if asset.origin == DataAssetOrigin.REGISTERED.value or _is_stale(asset, observation):
            return
        previous_updated_at: datetime = asset.updated_at
        was_deleted: bool = asset.state == DataAssetState.DELETED.value
        was_unknown: bool = asset.state == DataAssetState.UNKNOWN.value
        if was_deleted:
            await restore_asset(session, asset, self.repository, actor_ref, observation.observed_at)
        elif was_unknown:
            await resume_asset(session, asset, self.repository, actor_ref, observation.observed_at)
        previous_seen: datetime | None = asset.last_seen_at
        asset.last_seen_at = (
            max(ensure_utc(previous_seen), ensure_utc(observation.observed_at))
            if previous_seen
            else ensure_utc(observation.observed_at)
        )
        asset.sweep_covered = observation.sweep_covered
        if observation.display_title:
            asset.name = observation.display_title[:512]
        asset.updated_at = max(ensure_utc(previous_updated_at), ensure_utc(observation.observed_at))
        if asset.collector_config_id is None:
            asset.collector_config_id = observation.collector_config_id
        await self._record_diffs(session, asset, observation, actor_ref, values)

    async def _create(
        self, session: AsyncSession, observation: ResourceObservation
    ) -> tuple[DataAssetRow, bool]:
        """Create an asset row with a safe display name and allocated id."""
        number: int = await self.repository.next_asset_number(session)
        asset: DataAssetRow = DataAssetRow(
            id=new_id(),
            number=number,
            resource_key=observation.resource_key,
            source=observation.source,
            origin=DataAssetOrigin.DISCOVERED.value,
            kind=(
                observation.kind_hint
                or _default_kind(observation.source, observation.display_locator)
            ).value,
            state=DataAssetState.PRESENT.value,
            name=(observation.display_title or observation.display_locator)[:512],
            display_locator=observation.display_locator,
            collector_config_id=observation.collector_config_id,
            sweep_covered=observation.sweep_covered,
            item_count=None,
            first_seen_at=observation.observed_at,
            last_seen_at=observation.observed_at,
            deleted_at=None,
            overdue_notified_for=None,
            created_by=None,
            created_at=observation.observed_at,
            updated_at=observation.observed_at,
        )
        inserted: DataAssetRow = await self.repository.insert_asset(session, asset)
        return inserted, inserted.id == asset.id

    async def _record_created(
        self,
        session: AsyncSession,
        asset: DataAssetRow,
        observation: ResourceObservation,
        actor_ref: str,
        values: frozenset[tuple[str, int]],
    ) -> None:
        """Initialize item/value baselines and append discovery."""
        await self._apply_items(session, asset, observation)
        count_delta: CountDelta | None = None
        if observation.items is not None and observation.items.count is not None:
            count_delta = apply_count(asset, observation.items.count, observation.complete)
        value_delta: ItemDelta | None = await apply_values(
            session, asset, values, observation.complete, observation.observed_at
        )
        item_count_after: int | None = asset.item_count
        if count_delta is not None:
            item_count_after = count_delta.after
        value_count_after: int = value_delta.open_count if value_delta is not None else len(values)
        await self.repository.append_event(
            session,
            DataEventDraft(
                asset_id=asset.id,
                kind=DataEventKind.DISCOVERED,
                recorded_at=observation.observed_at,
                actor_ref=actor_ref,
                scan_job_id=observation.scan_job_id,
                item_count_after=item_count_after,
                value_count_after=value_count_after,
            ),
        )

    async def _record_diffs(
        self,
        session: AsyncSession,
        asset: DataAssetRow,
        observation: ResourceObservation,
        actor_ref: str,
        values: frozenset[tuple[str, int]],
    ) -> None:
        """Apply current item/value diffs and append changed-category events."""
        value_delta: ItemDelta | None = await apply_values(
            session, asset, values, observation.complete, observation.observed_at
        )
        if value_delta is not None and (value_delta.added or value_delta.removed):
            await self.repository.append_event(
                session,
                DataEventDraft(
                    asset_id=asset.id,
                    kind=DataEventKind.VALUES_CHANGED,
                    recorded_at=observation.observed_at,
                    actor_ref=actor_ref,
                    scan_job_id=observation.scan_job_id,
                    values_added=value_delta.added,
                    values_removed=value_delta.removed,
                    value_count_after=value_delta.open_count,
                ),
            )
        item_delta: ItemDelta | None = await self._apply_items(session, asset, observation)
        count_delta: CountDelta | None = None
        if observation.items is not None and observation.items.count is not None:
            count_delta = apply_count(asset, observation.items.count, observation.complete)
        if item_delta is not None and (item_delta.added or item_delta.removed):
            await self._append_item_change(
                session,
                asset,
                observation,
                actor_ref,
                item_delta.added,
                item_delta.removed,
                asset.item_count,
            )
        elif count_delta is not None and (
            count_delta.baseline or count_delta.added or count_delta.removed
        ):
            await self._append_item_change(
                session,
                asset,
                observation,
                actor_ref,
                count_delta.added,
                count_delta.removed,
                count_delta.after,
            )

    async def _apply_items(
        self, session: AsyncSession, asset: DataAssetRow, observation: ResourceObservation
    ) -> ItemDelta | None:
        """Apply key-based item diffs, if this observation carries keys."""
        items: ItemObservation | None = observation.items
        if items is None or items.fingerprints is None:
            return None
        return await apply_fingerprints(
            session,
            asset,
            DataItemKind.ITEM,
            frozenset(items.fingerprints),
            1,
            observation.complete,
            observation.observed_at,
        )

    async def _append_item_change(
        self,
        session: AsyncSession,
        asset: DataAssetRow,
        observation: ResourceObservation,
        actor_ref: str,
        added: int,
        removed: int,
        after: int | None,
    ) -> None:
        """Append one item change event."""
        await self.repository.append_event(
            session,
            DataEventDraft(
                asset_id=asset.id,
                kind=DataEventKind.ITEMS_CHANGED,
                recorded_at=observation.observed_at,
                actor_ref=actor_ref,
                scan_job_id=observation.scan_job_id,
                items_added=added,
                items_removed=removed,
                item_count_after=after,
            ),
        )


def _default_kind(source: str, locator: str) -> DataAssetKind:
    """Infer a broad asset shape from the safe source and display locator."""
    if source in ("gdrive", "gdocs", "github"):
        return DataAssetKind.FILE
    if source == "gsheets":
        return DataAssetKind.TABLE
    if source == "slack":
        return DataAssetKind.FILE if "/file/" in locator else DataAssetKind.OBJECT
    return DataAssetKind.OTHER


def _is_stale(asset: DataAssetRow, observation: ResourceObservation) -> bool:
    """Older source observations cannot reverse a newer lifecycle or value baseline."""
    watermark: datetime | None = (
        asset.deleted_at
        if asset.state == DataAssetState.DELETED.value
        else asset.updated_at
        if asset.state == DataAssetState.UNKNOWN.value
        else asset.last_seen_at
    )
    return watermark is not None and ensure_utc(observation.observed_at) < ensure_utc(watermark)
