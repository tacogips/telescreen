"""Handoff migrations preserve existing inventory and support incremental upgrade."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from telescreen.db.models import DataAssetRow
from tests.db.test_v14_migration import _alembic_config
from tests.inventory.core.test_query_contract import _asset
from tests.inventory.handoff.conftest import NOW

_HANDOFF_TABLES: set[str] = {
    "inventory_outbox_streams",
    "inventory_outbox",
    "inventory_inbox_streams",
    "inventory_inbox",
}


def test_incremental_handoff_upgrade_downgrade_preserves_asset_master(tmp_path: Path) -> None:
    """Run the new revision on an existing master and repeat its upgrade safely."""
    url: str = f"sqlite:///{tmp_path / 'handoff-migration.db'}"
    config: Config = _alembic_config(url)
    command.upgrade(config, "head")
    engine: Engine = create_engine(url)
    try:
        with Session(engine) as session:
            session.add(_asset("asset", 1, "Existing master", NOW))
            session.commit()
        command.downgrade(config, "0005_v1_4")
        assert _HANDOFF_TABLES.isdisjoint(inspect(engine).get_table_names())
        command.upgrade(config, "head")
        assert set(inspect(engine).get_table_names()) >= _HANDOFF_TABLES
        assert inspect(engine).get_pk_constraint("inventory_inbox")["constrained_columns"] == [
            "stream_id",
            "sequence",
        ]
        assert {"id"} == {
            name
            for constraint in inspect(engine).get_unique_constraints("inventory_inbox")
            for name in constraint["column_names"]
        }
        # Re-run the revision against existing tables, not merely Alembic's no-op head check.
        command.stamp(config, "0005_v1_4")
        command.upgrade(config, "head")
        with Session(engine) as session:
            assert session.scalar(select(DataAssetRow.name).where(DataAssetRow.id == "asset")) == (
                "Existing master"
            )
    finally:
        engine.dispose()
