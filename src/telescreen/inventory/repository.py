"""Persistence operations for data assets and their append-only events."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from telescreen.core.ids import new_id
from telescreen.core.vocabulary import DataEventKind
from telescreen.db.models import (
    CounterRow,
    DataAssetEventRow,
    DataAssetItemRow,
    DataAssetRow,
    DataFieldValueRow,
)


@dataclass(frozen=True)
class DataEventDraft:
    """Values for one immutable data asset event."""

    asset_id: str
    kind: DataEventKind
    recorded_at: datetime
    actor_ref: str
    occurred_at: datetime | None = None
    scan_job_id: str | None = None
    items_added: int | None = None
    items_removed: int | None = None
    item_count_after: int | None = None
    values_added: int | None = None
    values_removed: int | None = None
    value_count_after: int | None = None
    field_key: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class InventoryRepository:
    """Read and insert inventory rows within a caller-owned transaction."""

    async def next_asset_number(self, session: AsyncSession) -> int:
        """Lock and increment the asset number counter."""
        counter: CounterRow | None = await session.scalar(
            select(CounterRow).where(CounterRow.name == "data_asset_number").with_for_update()
        )
        if counter is None:
            counter = CounterRow(name="data_asset_number", value=0)
            session.add(counter)
            await session.flush()
        counter.value += 1
        return counter.value

    async def get(
        self, session: AsyncSession, asset_id: str, lock: bool = False
    ) -> DataAssetRow | None:
        """Read an asset by its public id, optionally locking it."""
        query: Select[tuple[DataAssetRow]] = select(DataAssetRow).where(DataAssetRow.id == asset_id)
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def get_by_resource_key(
        self, session: AsyncSession, resource_key: str, lock: bool = False
    ) -> DataAssetRow | None:
        """Read an asset by its opaque resource key, optionally locking it."""
        query: Select[tuple[DataAssetRow]] = select(DataAssetRow).where(
            DataAssetRow.resource_key == resource_key
        )
        if lock:
            query = query.with_for_update()
        return await session.scalar(query)

    async def insert_asset(self, session: AsyncSession, row: DataAssetRow) -> DataAssetRow:
        """Insert an asset and recover a concurrent unique-key insert."""
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
            return row
        except IntegrityError:
            existing: DataAssetRow | None = await self.get_by_resource_key(
                session, row.resource_key, lock=True
            )
            if existing is None:
                raise
            return existing

    async def append_event(self, session: AsyncSession, draft: DataEventDraft) -> DataAssetEventRow:
        """Insert one event; no update or delete operation is exposed."""
        row: DataAssetEventRow = DataAssetEventRow(
            id=new_id(),
            asset_id=draft.asset_id,
            kind=draft.kind.value,
            occurred_at=draft.occurred_at,
            recorded_at=draft.recorded_at,
            actor_ref=draft.actor_ref,
            scan_job_id=draft.scan_job_id,
            items_added=draft.items_added,
            items_removed=draft.items_removed,
            item_count_after=draft.item_count_after,
            values_added=draft.values_added,
            values_removed=draft.values_removed,
            value_count_after=draft.value_count_after,
            field_key=draft.field_key,
            old_value=draft.old_value,
            new_value=draft.new_value,
            note=draft.note,
        )
        session.add(row)
        await session.flush()
        return row

    async def item_rows(
        self, session: AsyncSession, asset_id: str, kind: str
    ) -> tuple[DataAssetItemRow, ...]:
        """Return all fingerprint rows for one asset and item kind."""
        return tuple(
            (
                await session.scalars(
                    select(DataAssetItemRow).where(
                        DataAssetItemRow.asset_id == asset_id, DataAssetItemRow.kind == kind
                    )
                )
            ).all()
        )

    async def value_rows(
        self, session: AsyncSession, asset_id: str
    ) -> tuple[DataFieldValueRow, ...]:
        """Return field values attached to an asset."""
        return tuple(
            (
                await session.scalars(
                    select(DataFieldValueRow).where(DataFieldValueRow.asset_id == asset_id)
                )
            ).all()
        )
