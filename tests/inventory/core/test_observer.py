"""Offline tests for data inventory observation and lifecycle behavior."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import json_object
from telescreen.core.vocabulary import (
    CollectorKind,
    DataAssetState,
    InventoryMode,
    TriggerMode,
)
from telescreen.db.models import DataAssetEventRow, DataAssetItemRow, DataAssetRow
from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.repository import InventoryRepository
from telescreen.ports.collectors import (
    CollectorConfig,
    CollectorConfigReader,
    CustomSelector,
)
from telescreen.ports.inventory import (
    ItemObservation,
    ResourceObservation,
    ValueFingerprint,
)
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class FakeConfigReader(CollectorConfigReader):
    """Return a fixed collector config for observer tests."""

    config: CollectorConfig | None

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        """Return the configured collector when its id matches."""
        del session
        return (
            self.config
            if self.config is not None and self.config.id == collector_config_id
            else None
        )

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        """Return the configured collector when its name matches."""
        del session
        return self.config if self.config is not None and self.config.name == name else None


def _config(mode: InventoryMode) -> CollectorConfig:
    """Build a safe custom collector config for inventory tests."""
    return CollectorConfig(
        id="config-id",
        name="test-config",
        kind=CollectorKind.CUSTOM,
        trigger_mode=TriggerMode.SCHEDULE,
        cron=None,
        sweep_cron=None,
        sweep_max_concurrency=1,
        selector=CustomSelector(json_object(())),
        credential_ref=None,
        enabled=True,
        inventory_mode=mode,
    )


def _observation(
    at: datetime,
    fingerprints: tuple[str, ...],
    complete: bool = True,
    removed: bool = False,
    values: tuple[ValueFingerprint, ...] = (),
    count: int | None = None,
) -> ResourceObservation:
    """Build one observation containing only safe keys and fingerprints."""
    items: ItemObservation | None = None
    if count is not None:
        items = ItemObservation(None, count)
    elif fingerprints:
        items = ItemObservation(fingerprints, None)
    return ResourceObservation(
        source="custom",
        resource_key="a" * 64,
        display_locator="report-id",
        display_title="Report",
        collector_config_id="config-id",
        scan_job_id=None,
        observed_at=at,
        complete=complete,
        removed=removed,
        sweep_covered=False,
        values=values,
        items=items,
        kind_hint=None,
    )


@pytest.mark.asyncio
async def test_observer_modes_and_discovery_baseline() -> None:
    """Sensitive-only suppresses clean assets while all mode records discovery."""
    harness: SqliteHarness = await make_sqlite_harness()
    at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    try:
        async with harness.sessions.begin() as session:
            sensitive: InventoryObserverImpl = InventoryObserverImpl(
                FakeConfigReader(_config(InventoryMode.SENSITIVE_ONLY)), InventoryRepository()
            )
            await sensitive.observe(session, _observation(at, (), count=10))
            no_asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
            assert no_asset is None
            disabled: InventoryObserverImpl = InventoryObserverImpl(
                FakeConfigReader(_config(InventoryMode.OFF)), InventoryRepository()
            )
            await disabled.observe(
                session, replace(_observation(at, (), count=10), resource_key="b" * 64)
            )
            assert await session.scalar(select(DataAssetRow)) is None

        async with harness.sessions.begin() as session:
            observer: InventoryObserverImpl = InventoryObserverImpl(
                FakeConfigReader(_config(InventoryMode.ALL)), InventoryRepository()
            )
            await observer.observe(session, _observation(at, (), count=10))
            asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
            assert asset is not None
            assert asset.number == 1
            assert asset.item_count == 10
            event: DataAssetEventRow | None = await session.scalar(select(DataAssetEventRow))
            assert event is not None
            assert event.kind == "discovered"
            assert event.item_count_after == 10
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_observer_item_removals_require_complete_observation() -> None:
    """Incomplete scans add fingerprints but do not infer removals."""
    harness: SqliteHarness = await make_sqlite_harness()
    at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    try:
        observer: InventoryObserverImpl = InventoryObserverImpl(
            FakeConfigReader(_config(InventoryMode.ALL)), InventoryRepository()
        )
        async with harness.sessions.begin() as session:
            await observer.observe(session, _observation(at, ("a" * 64, "b" * 64)))
            await observer.observe(
                session,
                _observation(at, ("a" * 64, "c" * 64), complete=False),
            )
            rows: tuple[DataAssetItemRow, ...] = tuple(
                (await session.scalars(select(DataAssetItemRow))).all()
            )
            assert len(rows) == 3
            assert all(row.removed_at is None for row in rows)
            events: tuple[DataAssetEventRow, ...] = tuple(
                (await session.scalars(select(DataAssetEventRow))).all()
            )
            assert tuple(event.kind for event in events) == (
                "discovered",
                "items_changed",
            )
            assert events[-1].items_added == 1
            assert events[-1].items_removed == 0
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_removed_observation_deletes_then_restores_asset() -> None:
    """Definitive removal closes an asset and a later sighting restores it."""
    harness: SqliteHarness = await make_sqlite_harness()
    at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    try:
        observer: InventoryObserverImpl = InventoryObserverImpl(
            FakeConfigReader(_config(InventoryMode.ALL)), InventoryRepository()
        )
        async with harness.sessions.begin() as session:
            await observer.observe(session, _observation(at, (), count=3))
            await observer.observe(session, _observation(at, (), removed=True))
            asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
            assert asset is not None
            assert asset.state == DataAssetState.DELETED.value
            assert asset.item_count == 0
            await observer.observe(session, _observation(at, (), count=2))
            assert asset.state == DataAssetState.PRESENT.value
            events: tuple[DataAssetEventRow, ...] = tuple(
                (
                    await session.scalars(select(DataAssetEventRow).order_by(DataAssetEventRow.id))
                ).all()
            )
            assert "deleted" in tuple(event.kind for event in events)
            assert "restored" in tuple(event.kind for event in events)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_observer_complete_item_and_count_diffs() -> None:
    """Complete removals are counted; incomplete key/count observations do not remove."""
    harness: SqliteHarness = await make_sqlite_harness()
    at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    observer: InventoryObserverImpl = InventoryObserverImpl(
        FakeConfigReader(_config(InventoryMode.ALL)), InventoryRepository()
    )
    try:
        async with harness.sessions.begin() as session:
            keys: tuple[str, ...] = tuple(f"{value:064x}" for value in range(5))
            await observer.observe(session, _observation(at, keys))
            await observer.observe(session, _observation(at, keys[:3]))
            rows: tuple[DataAssetItemRow, ...] = tuple(
                (await session.scalars(select(DataAssetItemRow))).all()
            )
            events: tuple[DataAssetEventRow, ...] = tuple(
                (await session.scalars(select(DataAssetEventRow))).all()
            )
            changed: DataAssetEventRow = events[-1]
            assert sum(row.removed_at is not None for row in rows) == 2
            assert (changed.items_removed, changed.item_count_after) == (2, 3)

            await observer.observe(session, _observation(at, keys[:1], complete=False))
            assert len((await session.scalars(select(DataAssetEventRow))).all()) == 2
            await observer.observe(
                session,
                _observation(at, (*keys[:1], f"{5:064x}"), complete=False),
            )
            item_events: tuple[DataAssetEventRow, ...] = tuple(
                (
                    await session.scalars(
                        select(DataAssetEventRow).where(
                            DataAssetEventRow.asset_id == events[0].asset_id
                        )
                    )
                ).all()
            )
            assert item_events[-1].items_added == 1
            assert item_events[-1].items_removed == 0
            assert item_events[-1].item_count_after == 4

            count_observation: ResourceObservation = replace(
                _observation(at, (), count=10), resource_key="b" * 64
            )
            await observer.observe(session, count_observation)
            count_observation = replace(
                count_observation,
                items=ItemObservation(None, 7),
            )
            await observer.observe(session, count_observation)
            count_observation = replace(
                count_observation,
                complete=False,
                items=ItemObservation(None, 2),
            )
            await observer.observe(session, count_observation)
            count_asset: DataAssetRow | None = await session.scalar(
                select(DataAssetRow).where(DataAssetRow.resource_key == "b" * 64)
            )
            assert count_asset is not None and count_asset.item_count == 7
            count_events: tuple[DataAssetEventRow, ...] = tuple(
                (
                    await session.scalars(
                        select(DataAssetEventRow).where(
                            DataAssetEventRow.asset_id == count_asset.id
                        )
                    )
                ).all()
            )
            assert tuple(event.kind for event in count_events) == (
                "discovered",
                "items_changed",
            )
            assert count_events[-1].items_removed == 3
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_observer_value_rebaseline_name_fallback_and_unknown_removal() -> None:
    """Value rotation is silent and names/unknown removals follow safe rules."""
    harness: SqliteHarness = await make_sqlite_harness()
    at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    observer: InventoryObserverImpl = InventoryObserverImpl(
        FakeConfigReader(_config(InventoryMode.SENSITIVE_ONLY)), InventoryRepository()
    )
    try:
        async with harness.sessions.begin() as session:
            values: tuple[ValueFingerprint, ...] = tuple(
                ValueFingerprint(f"{value:064x}", 1) for value in range(5)
            )
            value_observation: ResourceObservation = replace(
                _observation(at, (), values=values), resource_key="b" * 64
            )
            await observer.observe(session, value_observation)
            value_asset: DataAssetRow | None = await session.scalar(
                select(DataAssetRow).where(DataAssetRow.resource_key == "b" * 64)
            )
            assert value_asset is not None
            assert value_asset.number == 1
            assert value_asset.state == DataAssetState.PRESENT.value
            discovered: DataAssetEventRow | None = await session.scalar(
                select(DataAssetEventRow).where(DataAssetEventRow.asset_id == value_asset.id)
            )
            assert discovered is not None and discovered.value_count_after == 5

            await observer.observe(
                session,
                replace(
                    value_observation,
                    values=values[:2],
                    observed_at=at,
                ),
            )
            value_events: tuple[DataAssetEventRow, ...] = tuple(
                (
                    await session.scalars(
                        select(DataAssetEventRow).where(
                            DataAssetEventRow.asset_id == value_asset.id
                        )
                    )
                ).all()
            )
            assert value_events[-1].kind == "values_changed"
            assert value_events[-1].values_removed == 3
            await observer.observe(
                session,
                replace(
                    value_observation,
                    values=(ValueFingerprint("f" * 64, 2),),
                    observed_at=at,
                ),
            )
            value_events = tuple(
                (
                    await session.scalars(
                        select(DataAssetEventRow).where(
                            DataAssetEventRow.asset_id == value_asset.id
                        )
                    )
                ).all()
            )
            assert len(value_events) == 2

            locator_observation: ResourceObservation = replace(
                _observation(at, (), count=1),
                resource_key="c" * 64,
                display_locator="safe-locator",
                display_title="",
            )
            all_observer: InventoryObserverImpl = InventoryObserverImpl(
                FakeConfigReader(_config(InventoryMode.ALL)), InventoryRepository()
            )
            await all_observer.observe(session, locator_observation)
            locator_asset: DataAssetRow | None = await session.scalar(
                select(DataAssetRow).where(DataAssetRow.resource_key == "c" * 64)
            )
            assert locator_asset is not None and locator_asset.name == "safe-locator"
            older: datetime = datetime(2026, 10, 12, tzinfo=UTC)
            await all_observer.observe(
                session,
                replace(
                    locator_observation,
                    observed_at=older,
                    display_title="",
                    complete=False,
                ),
            )
            assert locator_asset.name == "safe-locator"
            assert locator_asset.last_seen_at is not None
            assert locator_asset.last_seen_at.replace(tzinfo=UTC) == at

            await observer.observe(
                session,
                replace(
                    _observation(at, (), removed=True),
                    resource_key="d" * 64,
                ),
            )
            unknown: DataAssetRow | None = await session.scalar(
                select(DataAssetRow).where(DataAssetRow.resource_key == "d" * 64)
            )
            assert unknown is None
    finally:
        await harness.close()
