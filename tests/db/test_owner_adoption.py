"""Checked legacy adoption preserves owner data and refuses incomplete migrations."""

from collections.abc import Callable
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Column, Engine, Integer, inspect, text
from sqlalchemy import create_engine as sync_engine
from sqlalchemy.orm import Session

from telescreen.cli import main
from telescreen.core.errors import ConfigError
from telescreen.db.adopt import adopt_owner
from telescreen.db.base import Base
from telescreen.db.migrate import upgrade, upgrade_store
from telescreen.db.models import CounterRow, InventoryCounterRow
from telescreen.db.ownership import Store
from tests.inventory.core.test_query_contract import _asset
from tests.inventory.handoff.conftest import NOW


@pytest.mark.parametrize("store", list(Store))
def test_adopt_and_repeat_preserve_records_and_allocations(tmp_path: Path, store: Store) -> None:
    """Adoption only adds owner migration state; it never recreates or reseeds business data."""
    path: Path = tmp_path / "legacy.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade(url)
    engine: Engine = sync_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            session.add(_asset("master-id", 11, "Master", NOW))
            inventory_counter: InventoryCounterRow | None = session.get(
                InventoryCounterRow, "data_asset_number"
            )
            main_counter: CounterRow | None = session.get(CounterRow, "issue_number")
            assert inventory_counter is not None and main_counter is not None
            inventory_counter.value = 21
            main_counter.value = 41
            session.commit()
        adopt_owner(url, store, writers_stopped=True)
        adopt_owner(url, store, writers_stopped=True)
        upgrade_store(url, store)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0009_inventory_counter"
            )
            assert connection.scalar(text(f"SELECT version_num FROM alembic_version_{store}")) == (
                f"{store}_0001"
            )
            assert connection.execute(text("SELECT id, number, name FROM data_assets")).one() == (
                "master-id",
                11,
                "Master",
            )
            assert connection.scalar(text("SELECT value FROM inventory_counters")) == 21
            assert (
                connection.scalar(text("SELECT value FROM counters WHERE name = 'issue_number'"))
                == 41
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("store", "mutation", "message"),
    (
        (Store.INVENTORY, "DROP INDEX ix_data_asset_events_asset_recorded", "indexes differ"),
        (Store.MAIN, "ALTER TABLE members ADD COLUMN future_probe INTEGER", "columns differ"),
        (Store.INVENTORY, "DELETE FROM inventory_counters", "allocation state"),
        (Store.MAIN, "UPDATE audit_chain_head SET hash = 'invalid'", "audit head"),
        (
            Store.INVENTORY,
            "UPDATE alembic_version SET version_num = '0008_retention_handoff'",
            "legacy revision",
        ),
    ),
)
def test_adoption_refuses_schema_or_state_drift_without_stamping(
    tmp_path: Path, store: Store, mutation: str, message: str
) -> None:
    """Do not turn a partially upgraded or damaged store into a supposedly valid baseline."""
    path: Path = tmp_path / "drift.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade(url)
    engine: Engine = sync_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(text(mutation))
        with pytest.raises(RuntimeError, match=message):
            adopt_owner(url, store, writers_stopped=True)
        assert f"alembic_version_{store}" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_failed_adoption_rolls_back_its_version_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation, version-table DDL and stamp share one transaction."""
    path: Path = tmp_path / "rollback.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade(url)
    original: Callable[[Config, str], None] = command.stamp

    def fail(config: Config, revision: str) -> None:
        original(config, revision)
        raise RuntimeError("stamp acknowledgment lost")

    with monkeypatch.context() as patch:
        patch.setattr(command, "stamp", fail)
        with pytest.raises(RuntimeError, match="stamp acknowledgment lost"):
            adopt_owner(url, Store.INVENTORY, writers_stopped=True)
    engine: Engine = sync_engine(f"sqlite:///{path}")
    try:
        assert "alembic_version_inventory" not in inspect(engine).get_table_names()
        adopt_owner(url, Store.INVENTORY, writers_stopped=True)
    finally:
        engine.dispose()


def test_adoption_contract_is_frozen_against_future_models(tmp_path: Path) -> None:
    """Replaying an old adoption remains possible after runtime models change."""
    path: Path = tmp_path / "frozen.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade(url)
    column: Column[int] = Column("future_probe", Integer, nullable=True)
    Base.metadata.tables["members"].append_column(column)
    try:
        adopt_owner(url, Store.MAIN, writers_stopped=True)
    finally:
        Base.metadata.tables["members"]._columns.remove(column)


def test_adoption_detects_a_filtered_index_with_the_same_name_and_columns(tmp_path: Path) -> None:
    """Index names and columns alone cannot hide a weakened partial-index definition."""
    path: Path = tmp_path / "filtered-index.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade(url)
    engine: Engine = sync_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_data_asset_events_asset_recorded"))
            connection.execute(
                text(
                    "CREATE INDEX ix_data_asset_events_asset_recorded "
                    "ON data_asset_events (asset_id, recorded_at) WHERE kind = 'deleted'"
                )
            )
        with pytest.raises(RuntimeError, match="indexes differ"):
            adopt_owner(url, Store.INVENTORY, writers_stopped=True)
        assert "alembic_version_inventory" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_cli_requires_explicit_owner_and_stopped_writers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator opts into adoption explicitly; ordinary migrate never silently adopts."""
    path: Path = tmp_path / "cli.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade(url)
    monkeypatch.setenv("TELESCREEN_DATABASE_URL", url)
    monkeypatch.delenv("TELESCREEN_INVENTORY_DATABASE_URL", raising=False)
    assert main(["migrate", "--adopt"]) == 3
    assert main(["migrate", "--adopt", "--store", "main"]) == 3
    assert main(["migrate", "--adopt", "--store", "main", "--writers-stopped"]) == 0
    assert main(["migrate", "--store", "main"]) == 0
    with pytest.raises(ConfigError, match="writers-stopped"):
        adopt_owner(url, Store.INVENTORY)
