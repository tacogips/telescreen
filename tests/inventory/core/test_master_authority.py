"""Registered master data and custom metadata remain authoritative during source replay."""

from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.vocabulary import DataAssetKind, InventoryMode
from telescreen.db.base import Base
from telescreen.db.models import DataAssetRow, DataFieldRow, DataFieldValueRow
from telescreen.db.ownership import INVENTORY_TABLES
from telescreen.db.session import create_sessionmaker
from telescreen.inventory.assets import AssetService
from telescreen.inventory.audit import InventoryAuditPublisher
from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.reconcile import InventoryReconcilerImpl
from telescreen.inventory.registration import RegistrationBody, RegistrationService
from telescreen.inventory.repository import InventoryRepository
from telescreen.inventory.values import ValueInput, ValueService, ValueValidator
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.inventory_types import ItemObservation, ResourceObservation
from tests.inventory.core.test_observer import _observation
from tests.inventory.fields.test_registration import EmptyRecipients
from tests.inventory.handoff.conftest import NOW


@pytest.mark.asyncio
async def test_registered_master_cannot_be_overwritten_or_deleted_by_collector_replay(
    tmp_path: Path,
) -> None:
    """Even a matching opaque key cannot give a collector authority over a registered asset."""
    engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}")
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)
    clock: FakeClock = FakeClock(NOW)
    redactor: Redactor = Redactor.default()
    audit: InventoryAuditPublisher = InventoryAuditPublisher(clock, redactor)
    repository: InventoryRepository = InventoryRepository()
    values: ValueService = ValueService(
        repository, ValueValidator(EmptyRecipients(), redactor), audit, clock
    )
    registration: RegistrationService = RegistrationService(
        repository, values, Fingerprinter(b"f" * 32, "test"), redactor, audit, clock
    )
    observer: InventoryObserverImpl = InventoryObserverImpl(repository)
    reconciler: InventoryReconcilerImpl = InventoryReconcilerImpl(repository, clock)
    try:
        async with engine.begin() as connection:
            for name in INVENTORY_TABLES:
                await connection.run_sync(Base.metadata.tables[name].create)
        async with sessions.begin() as session:
            session.add(
                DataFieldRow(
                    id="purpose",
                    key="purpose",
                    label="Purpose",
                    field_type="text",
                    required=False,
                    position=0,
                    created_by="member:admin",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await session.flush()
            asset_id: str = await registration.register(
                session,
                RegistrationBody(
                    "Business register",
                    "manual",
                    "register/table",
                    DataAssetKind.TABLE,
                    25,
                    (ValueInput("purpose", '"Finance"'),),
                ),
                "member:admin",
            )
            asset: DataAssetRow | None = await session.get(DataAssetRow, asset_id)
            assert asset is not None
            key: str = asset.resource_key
            # Simulate an incorrect correlation received from an older producer.
            asset.collector_config_id = "collector"
            asset.sweep_covered = True
        clock.advance(timedelta(minutes=1))
        observation: ResourceObservation = ResourceObservation(
            "custom",
            key,
            "collector/other",
            "Source title",
            "collector",
            "scan",
            clock.now(),
            True,
            False,
            True,
            (),
            ItemObservation(None, 2),
            DataAssetKind.FILE,
        )
        async with sessions.begin() as session:
            await observer.apply_observation(session, observation, InventoryMode.ALL)
            await observer.apply_observation(
                session, replace(observation, removed=True), InventoryMode.ALL
            )
            assert (
                await reconciler.reconcile_sweep(
                    session, "collector", clock.now(), mode=InventoryMode.ALL
                )
                == 0
            )
            assert (
                await reconciler.reconcile_listing(
                    session, "collector", frozenset(), mode=InventoryMode.ALL
                )
                == 0
            )
            assert await reconciler.stop_tracking(session, "collector", "member:admin") == 0
            asset = await session.get(DataAssetRow, asset_id)
            assert asset is not None
            assert (
                asset.id,
                asset.number,
                asset.name,
                asset.kind,
                asset.item_count,
                asset.state,
            ) == (asset_id, 1, "Business register", "table", 25, "present")
            value: DataFieldValueRow | None = await session.get(
                DataFieldValueRow, (asset_id, "purpose")
            )
            assert value is not None and value.value_json == '"Finance"'
        assets: AssetService = AssetService(repository, audit, redactor, clock)
        async with sessions.begin() as session:
            await assets.mark_deleted(session, asset_id, "member:admin", None, None)
        clock.advance(timedelta(minutes=1))
        async with sessions.begin() as session:
            await observer.apply_observation(
                session, replace(observation, observed_at=clock.now()), InventoryMode.ALL
            )
            asset = await session.get(DataAssetRow, asset_id)
            assert asset is not None and asset.state == "deleted"
            await assets.restore(session, asset_id, "member:admin")
            await assets.record_event(
                session, asset_id, clock.now(), "member:admin", None, None, 40, None
            )
        async with sessions.begin() as session:
            await observer.apply_observation(
                session, replace(observation, observed_at=clock.now()), InventoryMode.ALL
            )
            asset = await session.get(DataAssetRow, asset_id)
            assert asset is not None and asset.state == "present" and asset.item_count == 40
            assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_discovery_cannot_overwrite_custom_kind_or_move_master_timestamp_backwards(
    tmp_path: Path,
) -> None:
    """Older source observations may update derived state but retain newer master edits."""
    engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}")
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)
    clock: FakeClock = FakeClock(NOW)
    repository: InventoryRepository = InventoryRepository()
    redactor: Redactor = Redactor.default()
    assets: AssetService = AssetService(
        repository, InventoryAuditPublisher(clock, redactor), redactor, clock
    )
    observer: InventoryObserverImpl = InventoryObserverImpl(repository)
    try:
        async with engine.begin() as connection:
            for name in INVENTORY_TABLES:
                await connection.run_sync(Base.metadata.tables[name].create)
        async with sessions.begin() as session:
            await observer.apply_observation(
                session, _observation(NOW, (), count=3), InventoryMode.ALL
            )
            asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
            assert asset is not None
            asset_id: str = asset.id
        clock.advance(timedelta(minutes=10))
        async with sessions.begin() as session:
            await assets.patch(session, asset_id, None, "table", "member:admin")
        async with sessions.begin() as session:
            await observer.apply_observation(
                session,
                replace(
                    _observation(NOW + timedelta(minutes=5), (), count=4),
                    kind_hint=DataAssetKind.FILE,
                ),
                InventoryMode.ALL,
            )
            await observer.apply_observation(
                session,
                _observation(NOW + timedelta(minutes=6), (), removed=True),
                InventoryMode.ALL,
            )
            await observer.apply_observation(
                session, _observation(NOW + timedelta(minutes=7), (), count=5), InventoryMode.ALL
            )
            asset = await session.get(DataAssetRow, asset_id)
            assert asset is not None and asset.kind == "table" and asset.state == "present"
            assert ensure_utc(asset.updated_at) == clock.now()
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class InsertRaceRepository(InventoryRepository):
    """Simulate a registered asset committing after the observer's initial missing read."""

    reads: list[bool]

    async def get_by_resource_key(
        self, session: AsyncSession, resource_key: str, lock: bool = False
    ) -> DataAssetRow | None:
        """The insert then hits the real database uniqueness check and reloads the winner."""
        if not self.reads:
            self.reads.append(True)
            return None
        return await super().get_by_resource_key(session, resource_key, lock)


@pytest.mark.asyncio
async def test_registered_insert_race_keeps_master_authority(tmp_path: Path) -> None:
    """A unique-key race must not bypass the registered-asset guard on reload."""
    engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}")
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)
    try:
        async with engine.begin() as connection:
            for name in INVENTORY_TABLES:
                await connection.run_sync(Base.metadata.tables[name].create)
        async with sessions.begin() as session:
            session.add(
                DataAssetRow(
                    id="registered",
                    number=1,
                    resource_key="a" * 64,
                    source="manual",
                    origin="registered",
                    kind="table",
                    state="present",
                    name="Master",
                    display_locator="register/table",
                    item_count=25,
                    first_seen_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            # The migration/allocator already knows about this registration.
            from telescreen.db.models import InventoryCounterRow

            session.add(InventoryCounterRow(name="data_asset_number", value=1))
        observer: InventoryObserverImpl = InventoryObserverImpl(InsertRaceRepository([]))
        async with sessions.begin() as session:
            await observer.apply_observation(
                session, _observation(NOW + timedelta(minutes=1), (), count=2), InventoryMode.ALL
            )
            asset: DataAssetRow | None = await session.get(DataAssetRow, "registered")
            assert asset is not None
            assert (asset.name, asset.kind, asset.item_count, asset.state) == (
                "Master",
                "table",
                25,
                "present",
            )
            assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 1
    finally:
        await engine.dispose()
