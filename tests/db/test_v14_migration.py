"""Regression tests for the idempotent v1.4 data inventory migration."""

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, Inspector
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import Session

from telescreen.db.models import DataAssetEventRow

V14_TABLES: tuple[str, ...] = (
    "data_assets",
    "data_asset_events",
    "data_asset_items",
    "data_fields",
    "data_field_values",
)
AT: datetime = datetime(2026, 10, 13, tzinfo=UTC)


def _alembic_config(database_url: str) -> Config:
    """Build an Alembic config for a temporary SQLite database."""
    migration_directory: Path = Path(__file__).parents[2] / "src/telescreen/db/migrations"
    config: Config = Config()
    config.set_main_option("script_location", migration_directory.as_posix())
    async_database_url: str = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    config.set_main_option("sqlalchemy.url", async_database_url.replace("%", "%%"))
    return config


def _assert_v14_schema(engine: Engine) -> None:
    """Assert the inventory tables, column and key indexes are present."""
    inspector: Inspector = inspect(engine)
    table_names: set[str] = set(inspector.get_table_names())
    assert set(V14_TABLES) <= table_names
    config_columns: set[str] = {
        column["name"] for column in inspector.get_columns("collector_configs")
    }
    assert "inventory_mode" in config_columns
    # Head also includes the four durable inventory handoff tables and the twelve
    # service split tables of 0010_service_split.
    assert len(table_names - {"alembic_version"}) == 74
    assert "uq_data_asset_items_key" in {
        str(constraint["name"])
        for constraint in inspector.get_unique_constraints("data_asset_items")
    }


def test_fresh_v14_schema_seeds_counter_and_upgrade_is_idempotent(tmp_path: Path) -> None:
    """Upgrade a fresh database, seed the counter, then safely apply head again."""
    database_file: Path = tmp_path / "fresh-v14.sqlite"
    database_url: str = f"sqlite:///{database_file}"
    config: Config = _alembic_config(database_url)
    command.upgrade(config, "head")
    engine: Engine = create_engine(database_url)
    _assert_v14_schema(engine)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT name, value FROM counters WHERE name = 'data_asset_number'")
        ).one() == ("data_asset_number", 0)
    command.upgrade(config, "head")
    _assert_v14_schema(engine)
    engine.dispose()


def test_legacy_v13_database_adds_default_and_downgrades_cleanly(tmp_path: Path) -> None:
    """Add the default to an existing collector and preserve it through downgrade."""
    database_file: Path = tmp_path / "legacy-v13.sqlite"
    database_url: str = f"sqlite:///{database_file}"
    config: Config = _alembic_config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "0004_v1_3")
    engine: Engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO collector_configs "
                "(id, name, kind, trigger_mode, sweep_max_concurrency, selector_json, enabled, "
                "created_at, updated_at) "
                "VALUES ('config-1', 'Config', 'custom', 'manual', 16, '{}', 1, :at, :at)"
            ),
            {"at": AT.isoformat()},
        )
    command.upgrade(config, "head")
    _assert_v14_schema(engine)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT inventory_mode FROM collector_configs WHERE id = 'config-1'")
            )
            == "sensitive_only"
        )
    command.downgrade(config, "0004_v1_3")
    inspector: Inspector = inspect(engine)
    assert not set(V14_TABLES) & set(inspector.get_table_names())
    assert "inventory_mode" not in {
        column["name"] for column in inspector.get_columns("collector_configs")
    }
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT id FROM collector_configs WHERE id = 'config-1'"))
            == "config-1"
        )
    engine.dispose()


def test_inventory_unique_keys_reject_duplicates(tmp_path: Path) -> None:
    """Enforce unique asset identity and fingerprint identity in SQLite."""
    database_file: Path = tmp_path / "unique-v14.sqlite"
    database_url: str = f"sqlite:///{database_file}"
    config: Config = _alembic_config(database_url)
    command.upgrade(config, "head")
    engine: Engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO data_assets "
                "(id, number, resource_key, source, origin, kind, state, name, display_locator, "
                "first_seen_at, created_at, updated_at) "
                "VALUES ('asset-1', 1, :key, 'manual', 'registered', 'dataset', 'present', "
                "'Name', 'locator', :at, :at, :at)"
            ),
            {"key": "a" * 64, "at": AT.isoformat()},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO data_assets "
                    "(id, number, resource_key, source, origin, kind, state, name, "
                    "display_locator, "
                    "first_seen_at, created_at, updated_at) VALUES "
                    "('asset-2', 2, :key, 'manual', 'registered', 'dataset', 'present', "
                    "'Name', 'locator', :at, :at, :at)"
                ),
                {"key": "a" * 64, "at": AT.isoformat()},
            )
    with Session(engine) as session:
        session.add(
            DataAssetEventRow(
                id="event-1",
                asset_id="asset-1",
                kind="discovered",
                recorded_at=AT,
                actor_ref="system",
            )
        )
        session.commit()
        session.execute(
            text(
                "INSERT INTO data_asset_items "
                "(id, asset_id, kind, fingerprint, first_seen_at) "
                "VALUES ('item-1', 'asset-1', 'key', :fingerprint, :at)"
            ),
            {"fingerprint": "b" * 64, "at": AT.isoformat()},
        )
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO data_asset_items "
                    "(id, asset_id, kind, fingerprint, first_seen_at) "
                    "VALUES ('item-2', 'asset-1', 'key', :fingerprint, :at)"
                ),
                {"fingerprint": "b" * 64, "at": AT.isoformat()},
            )
            session.commit()
    engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_data_asset_events_reject_update_and_delete() -> None:
    """Verify the PostgreSQL data-event trigger rejects mutation when configured."""
    database_url: str | None = os.environ.get("TELESCREEN_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("requires TELESCREEN_TEST_DATABASE_URL")
    await asyncio.to_thread(command.upgrade, _alembic_config(database_url), "head")
    engine: AsyncEngine = create_async_engine(database_url)
    event_id: str = f"event-{time.time_ns()}"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO data_asset_events "
                "(id, asset_id, kind, recorded_at, actor_ref) "
                "VALUES (:id, 'integration-asset', 'discovered', :at, 'integration')"
            ),
            {"id": event_id, "at": AT},
        )
    async with engine.begin() as connection:
        with pytest.raises(SQLAlchemyError, match="append-only"):
            await connection.execute(
                text("UPDATE data_asset_events SET note = 'changed' WHERE id = :id"),
                {"id": event_id},
            )
    async with engine.begin() as connection:
        with pytest.raises(SQLAlchemyError, match="append-only"):
            await connection.execute(
                text("DELETE FROM data_asset_events WHERE id = :id"), {"id": event_id}
            )
    await engine.dispose()
