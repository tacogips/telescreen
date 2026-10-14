"""Offline tests for authoritative inventory reconciliation."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import (
    DataAssetOrigin,
    DataAssetState,
    InventoryMode,
)
from telescreen.db.models import DataAssetEventRow, DataAssetRow
from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.reconcile import InventoryReconcilerImpl
from telescreen.inventory.repository import InventoryRepository
from telescreen.ports.inventory import ItemObservation, ResourceObservation
from tests.support.db import SqliteHarness, make_sqlite_harness


def _asset(
    asset_id: str,
    number: int,
    at: datetime,
    *,
    config_id: str = "config-id",
    covered: bool = True,
    origin: DataAssetOrigin = DataAssetOrigin.DISCOVERED,
    state: DataAssetState = DataAssetState.PRESENT,
) -> DataAssetRow:
    """Create a complete asset row for one reconciliation case."""
    return DataAssetRow(
        id=asset_id,
        number=number,
        resource_key=str(number).zfill(64),
        source="custom",
        origin=origin.value,
        kind="other",
        state=state.value,
        name=asset_id,
        display_locator=asset_id,
        collector_config_id=config_id,
        sweep_covered=covered,
        item_count=2,
        first_seen_at=at,
        last_seen_at=at,
        deleted_at=None,
        overdue_notified_for=None,
        created_by=None,
        created_at=at,
        updated_at=at,
    )


@pytest.mark.asyncio
async def test_reconcile_sweep_deletes_only_covered_assets_before_watermark() -> None:
    """Sweep absence deletes only covered, old, present assets for that config."""
    harness: SqliteHarness = await make_sqlite_harness()
    before: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    watermark: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    clock: FakeClock = FakeClock(watermark)
    reconciler: InventoryReconcilerImpl = InventoryReconcilerImpl(InventoryRepository(), clock)
    try:
        async with harness.sessions.begin() as session:
            session.add_all(
                (
                    _asset("old-covered", 1, before),
                    _asset("new-covered", 2, watermark + timedelta(hours=1)),
                    _asset("uncovered", 3, before, covered=False),
                    _asset("other-config", 4, before, config_id="other"),
                )
            )
            await session.flush()
            assert (
                await reconciler.reconcile_sweep(
                    session, "config-id", watermark, mode=InventoryMode.ALL
                )
                == 1
            )
            deleted: DataAssetRow | None = await session.get(DataAssetRow, "old-covered")
            new: DataAssetRow | None = await session.get(DataAssetRow, "new-covered")
            uncovered: DataAssetRow | None = await session.get(DataAssetRow, "uncovered")
            other: DataAssetRow | None = await session.get(DataAssetRow, "other-config")
            assert deleted is not None and deleted.state == DataAssetState.DELETED.value
            assert new is not None and new.state == DataAssetState.PRESENT.value
            assert uncovered is not None and uncovered.state == DataAssetState.PRESENT.value
            assert other is not None and other.state == DataAssetState.PRESENT.value
            event: DataAssetEventRow | None = await session.scalar(select(DataAssetEventRow))
            assert event is not None
            assert event.kind == "deleted"
            assert event.actor_ref == "collector:config-id"

        off_reconciler: InventoryReconcilerImpl = InventoryReconcilerImpl(
            InventoryRepository(), clock
        )
        async with harness.sessions.begin() as session:
            assert (
                await off_reconciler.reconcile_sweep(
                    session, "config-id", watermark, mode=InventoryMode.OFF
                )
                == 0
            )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_reconcile_listing_deletes_only_unlisted_covered_assets() -> None:
    """A full listing preserves listed assets and deletes omitted candidates."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    reconciler: InventoryReconcilerImpl = InventoryReconcilerImpl(
        InventoryRepository(), FakeClock(now)
    )
    try:
        async with harness.sessions.begin() as session:
            session.add_all(
                (
                    _asset("listed", 1, now),
                    _asset("omitted", 2, now),
                    _asset("uncovered", 3, now, covered=False),
                    _asset("other-config", 4, now, config_id="other"),
                )
            )
            await session.flush()
            assert (
                await reconciler.reconcile_listing(
                    session, "config-id", frozenset({str(1).zfill(64)}), mode=InventoryMode.ALL
                )
                == 1
            )
            states: tuple[tuple[str, str], ...] = tuple(
                (row.id, row.state)
                for row in (
                    await session.scalars(select(DataAssetRow).order_by(DataAssetRow.id))
                ).all()
            )
            assert states == (
                ("listed", DataAssetState.PRESENT.value),
                ("omitted", DataAssetState.DELETED.value),
                ("other-config", DataAssetState.PRESENT.value),
                ("uncovered", DataAssetState.PRESENT.value),
            )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_stop_tracking_marks_discovered_assets_unknown_and_observation_resumes() -> None:
    """Stopping affects present discovered assets and a later sighting resumes them."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    repository: InventoryRepository = InventoryRepository()
    clock: FakeClock = FakeClock(now)
    reconciler: InventoryReconcilerImpl = InventoryReconcilerImpl(repository, clock)
    try:
        async with harness.sessions.begin() as session:
            session.add_all(
                (
                    _asset("discovered", 1, now),
                    _asset("registered", 2, now, origin=DataAssetOrigin.REGISTERED),
                )
            )
            await session.flush()
            assert await reconciler.stop_tracking(session, "config-id", "member:admin") == 1
            discovered: DataAssetRow | None = await session.get(DataAssetRow, "discovered")
            registered: DataAssetRow | None = await session.get(DataAssetRow, "registered")
            assert discovered is not None and discovered.state == DataAssetState.UNKNOWN.value
            assert registered is not None and registered.state == DataAssetState.PRESENT.value
            events: tuple[DataAssetEventRow, ...] = tuple(
                (await session.scalars(select(DataAssetEventRow))).all()
            )
            assert len(events) == 1
            assert events[0].kind == "tracking_stopped"
            assert events[0].actor_ref == "member:admin"

            observation: ResourceObservation = ResourceObservation(
                source="custom",
                resource_key=str(1).zfill(64),
                display_locator="discovered",
                display_title="Discovered",
                collector_config_id="config-id",
                scan_job_id=None,
                observed_at=now,
                complete=True,
                removed=False,
                sweep_covered=False,
                values=(),
                items=ItemObservation(None, 2),
                kind_hint=None,
            )
            observer: InventoryObserverImpl = InventoryObserverImpl(repository)
            clock.advance(timedelta(minutes=1))
            observation = replace(observation, observed_at=clock.now())
            await observer.apply_observation(session, observation, InventoryMode.ALL)
            assert discovered.state == DataAssetState.PRESENT.value
            events = tuple(
                (
                    await session.scalars(
                        select(DataAssetEventRow).order_by(DataAssetEventRow.recorded_at)
                    )
                ).all()
            )
            assert tuple(event.kind for event in events) == (
                "tracking_stopped",
                "tracking_resumed",
            )
    finally:
        await harness.close()
