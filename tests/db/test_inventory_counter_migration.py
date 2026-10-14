"""Asset numbering migrations preserve IDs and allocation high-water marks both ways."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from telescreen.db.models import CounterRow, DataAssetRow, InventoryCounterRow
from tests.db.test_v14_migration import _alembic_config
from tests.inventory.core.test_query_contract import _asset
from tests.inventory.handoff.conftest import NOW


@pytest.mark.parametrize(("legacy", "asset_max", "expected"), ((41, 9, 41), (3, 19, 19)))
def test_counter_upgrade_and_downgrade_preserve_greater_high_water_mark(
    tmp_path: Path, legacy: int, asset_max: int, expected: int
) -> None:
    """Never renumber assets, reuse prior allocations, or lower an advanced counter."""
    url: str = f"sqlite:///{tmp_path / 'inventory-counter.db'}"
    config: Config = _alembic_config(url)
    command.upgrade(config, "0008_retention_handoff")
    engine: Engine = create_engine(url)
    try:
        assert "inventory_counters" not in inspect(engine).get_table_names()
        with Session(engine) as session:
            counter: CounterRow | None = session.get(CounterRow, "data_asset_number")
            assert counter is not None
            counter.value = legacy
            session.add(_asset("master-id", asset_max, "Master", NOW))
            session.commit()
        command.upgrade(config, "head")
        with Session(engine) as session:
            own: InventoryCounterRow | None = session.get(InventoryCounterRow, "data_asset_number")
            assert own is not None and own.value == expected
            own.value += 10
            session.commit()
        command.stamp(config, "0008_retention_handoff")
        command.upgrade(config, "head")
        with Session(engine) as session:
            own = session.get(InventoryCounterRow, "data_asset_number")
            assert own is not None and own.value == expected + 10
            assert session.scalar(select(DataAssetRow.id)) == "master-id"
            assert session.scalar(select(DataAssetRow.number)) == asset_max
        command.downgrade(config, "0008_retention_handoff")
        assert "inventory_counters" not in inspect(engine).get_table_names()
        with Session(engine) as session:
            counter = session.get(CounterRow, "data_asset_number")
            assert counter is not None and counter.value == expected + 10
        command.upgrade(config, "head")
        with Session(engine) as session:
            own = session.get(InventoryCounterRow, "data_asset_number")
            assert own is not None and own.value == expected + 10
    finally:
        engine.dispose()
