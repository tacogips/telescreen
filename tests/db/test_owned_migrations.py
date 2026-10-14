"""Independent migration histories create exactly one owner's schema and seeds."""

import ast
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Column, Engine, Integer, inspect, text
from sqlalchemy import create_engine as sync_engine

from telescreen.db.base import Base
from telescreen.db.migrate import owner_config, upgrade, upgrade_configured, upgrade_store
from telescreen.db.models import InventoryCounterRow
from telescreen.db.owned_migrations import baseline
from telescreen.db.ownership import Store, tables_for


@pytest.mark.parametrize("store", list(Store))
def test_owner_upgrade_repeat_and_downgrade(tmp_path: Path, store: Store) -> None:
    """Install the real packaged snapshot and compare all columns and indexes."""
    path: Path = tmp_path / f"{store}.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade_store(url, store)
    upgrade_store(url, store)
    engine: Engine = sync_engine(f"sqlite:///{path}")
    try:
        assert InventoryCounterRow.__tablename__ == "inventory_counters"
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) == {
                *tables_for(store),
                f"alembic_version_{store}",
            }
            for name in tables_for(store):
                expected_columns: set[tuple[str, bool]] = {
                    (column.name, bool(column.nullable))
                    for column in Base.metadata.tables[name].columns
                }
                actual_columns: set[tuple[str, bool]] = {
                    (column["name"], column["nullable"])
                    for column in inspect(connection).get_columns(name)
                }
                assert actual_columns == expected_columns
                expected_indexes: set[tuple[str | None, tuple[str, ...], bool]] = {
                    (index.name, tuple(column.name for column in index.columns), index.unique)
                    for index in Base.metadata.tables[name].indexes
                }
                actual_indexes: set[tuple[str | None, tuple[str | None, ...], bool]] = {
                    (index["name"], tuple(index["column_names"]), bool(index["unique"]))
                    for index in inspect(connection).get_indexes(name)
                }
                assert actual_indexes == expected_indexes
            if store is Store.MAIN:
                assert connection.execute(text("SELECT value FROM counters")).scalar_one() == 0
                assert connection.execute(
                    text("SELECT hash FROM audit_chain_head")
                ).scalar_one() == ("0" * 64)
            else:
                assert (
                    connection.execute(text("SELECT value FROM inventory_counters")).scalar_one()
                    == 0
                )
        command.downgrade(owner_config(url, store), "base")
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) == {f"alembic_version_{store}"}
        upgrade_store(url, store)
    finally:
        engine.dispose()


def test_default_shared_migrate_continues_the_two_owner_histories(tmp_path: Path) -> None:
    """An adopted or newly composed shared owner deployment must not replay legacy creation."""
    path: Path = tmp_path / "owned-shared.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade_store(url, Store.MAIN)
    upgrade_store(url, Store.INVENTORY)
    upgrade_configured(url)
    upgrade_configured(url, url)
    engine: Engine = sync_engine(f"sqlite:///{path}")
    try:
        assert "alembic_version" not in inspect(engine).get_table_names()
        assert {"alembic_version_main", "alembic_version_inventory"} <= set(
            inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()


def test_owner_histories_can_share_a_database_without_sharing_versions(tmp_path: Path) -> None:
    """Downgrading inventory leaves main tables, state and migration head intact."""
    path: Path = tmp_path / "both.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade_store(url, Store.MAIN)
    upgrade_store(url, Store.INVENTORY)
    engine: Engine = sync_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE counters SET value = 42"))
        command.downgrade(owner_config(url, Store.INVENTORY), "base")
        upgrade_store(url, Store.MAIN)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT value FROM counters")).scalar_one() == 42
            assert connection.execute(
                text("SELECT version_num FROM alembic_version_main")
            ).scalar_one() == ("main_0001")
            assert not set(tables_for(Store.INVENTORY)).intersection(
                inspect(connection).get_table_names()
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("store", list(Store))
def test_owner_baseline_refuses_implicit_legacy_adoption(tmp_path: Path, store: Store) -> None:
    """An existing shared database cannot accidentally become an empty split install."""
    path: Path = tmp_path / "legacy.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade(url)
    with pytest.raises(RuntimeError, match="existing schema needs adoption"):
        upgrade_store(url, store)
    engine: Engine = sync_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            assert set(tables_for(store)) <= set(inspect(connection).get_table_names())
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0009_inventory_counter")
    finally:
        engine.dispose()


def test_frozen_baseline_ignores_new_model_columns(tmp_path: Path) -> None:
    """Changing future ORM metadata cannot rewrite an already released owner revision."""
    column: Column[int] = Column("future_probe", Integer, nullable=True)
    Base.metadata.tables["inventory_counters"].append_column(column)
    path: Path = tmp_path / "frozen.db"
    try:
        upgrade_store(f"sqlite+aiosqlite:///{path}", Store.INVENTORY)
        engine: Engine = sync_engine(f"sqlite:///{path}")
        try:
            with engine.connect() as connection:
                assert "future_probe" not in {
                    item["name"] for item in inspect(connection).get_columns("inventory_counters")
                }
        finally:
            engine.dispose()
    finally:
        Base.metadata.tables["inventory_counters"]._columns.remove(column)


def test_owner_migrations_never_import_live_models_or_ownership() -> None:
    """Historical table sets and definitions stay frozen when application code changes."""
    root: Path = Path(__file__).parents[2] / "src/telescreen/db/owned_migrations"
    for path in root.rglob("*.py"):
        tree: ast.Module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in (
                    "telescreen.db.models",
                    "telescreen.db.base",
                    "telescreen.db.ownership",
                ), path


@pytest.mark.parametrize("store", list(Store))
def test_failed_baseline_rolls_back_ddl_seeds_and_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    """A late failure leaves an empty retryable store even with SQLite legacy DDL mode."""
    path: Path = tmp_path / "rollback.db"
    url: str = f"sqlite+aiosqlite:///{path}"

    def fail(_table: str, *, drop: bool = False) -> None:
        del drop
        raise RuntimeError("late migration failure")

    with monkeypatch.context() as patch:
        patch.setattr(baseline, "append_only", fail)
        with pytest.raises(RuntimeError, match="late migration failure"):
            upgrade_store(url, store)
    engine: Engine = sync_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == []
        upgrade_store(url, store)
    finally:
        engine.dispose()
