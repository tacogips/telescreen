"""Data asset lifecycle state transitions and audit events."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.vocabulary import DataAssetState, DataEventKind
from telescreen.db.models import DataAssetRow
from telescreen.inventory.items import close_all
from telescreen.inventory.repository import DataEventDraft, InventoryRepository


async def delete_asset(
    session: AsyncSession,
    asset: DataAssetRow,
    repository: InventoryRepository,
    actor_ref: str,
    at: datetime,
    scan_job_id: str | None = None,
    occurred_at: datetime | None = None,
    note: str | None = None,
) -> None:
    """Mark an asset deleted, close open fingerprints, and append one event."""
    items_removed: int
    values_removed: int
    items_removed, values_removed = await close_all(session, asset, at)
    if asset.item_count is not None:
        items_removed = max(items_removed, asset.item_count)
    asset.state = DataAssetState.DELETED.value
    asset.deleted_at = at
    asset.updated_at = max(ensure_utc(asset.updated_at), ensure_utc(at))
    if asset.item_count is not None:
        asset.item_count = 0
    await repository.append_event(
        session,
        DataEventDraft(
            asset_id=asset.id,
            kind=DataEventKind.DELETED,
            recorded_at=at,
            actor_ref=actor_ref,
            occurred_at=occurred_at,
            scan_job_id=scan_job_id,
            items_removed=items_removed,
            item_count_after=asset.item_count,
            values_removed=values_removed,
            value_count_after=0,
            note=note,
        ),
    )


async def restore_asset(
    session: AsyncSession,
    asset: DataAssetRow,
    repository: InventoryRepository,
    actor_ref: str,
    at: datetime,
    occurred_at: datetime | None = None,
    note: str | None = None,
) -> None:
    """Restore a deleted asset without reopening its item history."""
    asset.state = DataAssetState.PRESENT.value
    asset.deleted_at = None
    asset.updated_at = max(ensure_utc(asset.updated_at), ensure_utc(at))
    await repository.append_event(
        session,
        DataEventDraft(
            asset_id=asset.id,
            kind=DataEventKind.RESTORED,
            recorded_at=at,
            actor_ref=actor_ref,
            occurred_at=occurred_at,
            note=note,
        ),
    )


async def stop_tracking_asset(
    session: AsyncSession,
    asset: DataAssetRow,
    repository: InventoryRepository,
    actor_ref: str,
    at: datetime,
) -> None:
    """Mark tracking unknown and append a tracking-stopped event."""
    asset.state = DataAssetState.UNKNOWN.value
    asset.updated_at = max(ensure_utc(asset.updated_at), ensure_utc(at))
    await repository.append_event(
        session,
        DataEventDraft(
            asset_id=asset.id,
            kind=DataEventKind.TRACKING_STOPPED,
            recorded_at=at,
            actor_ref=actor_ref,
        ),
    )


async def resume_asset(
    session: AsyncSession,
    asset: DataAssetRow,
    repository: InventoryRepository,
    actor_ref: str,
    at: datetime,
) -> None:
    """Mark tracking present and append a tracking-resumed event."""
    asset.state = DataAssetState.PRESENT.value
    asset.deleted_at = None
    asset.updated_at = max(ensure_utc(asset.updated_at), ensure_utc(at))
    await repository.append_event(
        session,
        DataEventDraft(
            asset_id=asset.id,
            kind=DataEventKind.TRACKING_RESUMED,
            recorded_at=at,
            actor_ref=actor_ref,
        ),
    )
