"""Regression tests for the idempotent three-service schema migration."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from telescreen.db.adopt import adopt_owner
from telescreen.db.migrate import owner_config, upgrade, upgrade_configured, upgrade_store
from telescreen.db.ownership import Store

SERVICE_SPLIT_TABLES: frozenset[str] = frozenset(
    {
        "notification_outbox",
        "inventory_notification_outbox",
        "notification_inbox",
        "notification_delivery_state",
        "projection_changes",
        "inventory_directory_entries",
        "notification_directory_entries",
        "inventory_issue_links",
        "inventory_audit_records",
        "inventory_audit_chain_head",
        "notification_audit_records",
        "notification_audit_chain_head",
    }
)
NOTIFICATION_ONLY_TABLES: frozenset[str] = frozenset(
    {
        "notification_inbox",
        "notification_delivery_state",
        "notification_directory_entries",
        "notification_audit_records",
        "notification_audit_chain_head",
    }
)
RETAINED_NOTIFICATION_TABLES: frozenset[str] = frozenset(
    {
        "notifications",
        "notification_templates",
        "notification_settings",
        "retention_notification_receipts",
    }
)


def _config(database_url: str) -> Config:
    """Build an Alembic config for a temporary SQLite database."""
    migration_directory: Path = Path(__file__).parents[2] / "src/telescreen/db/migrations"
    config: Config = Config()
    config.set_main_option("script_location", migration_directory.as_posix())
    config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("%", "%%").replace("sqlite://", "sqlite+aiosqlite://", 1),
    )
    return config


def test_service_split_upgrade_is_idempotent_and_downgrades_only_its_tables(
    tmp_path: Path,
) -> None:
    """The new revision adds twelve tables and seeds, then removes only those additions."""
    database_url: str = f"sqlite:///{tmp_path / 'service-split.db'}"
    config: Config = _config(database_url)
    async_database_url: str = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    upgrade(async_database_url, "0009_inventory_counter")
    command.upgrade(config, "head")
    engine: Engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            names: set[str] = set(inspect(connection).get_table_names()) - {"alembic_version"}
            assert len(names) == 74
            assert names >= SERVICE_SPLIT_TABLES
            assert (
                connection.execute(
                    text("SELECT value FROM counters WHERE name = 'directory_revision'")
                ).scalar_one()
                == 0
            )
            assert connection.execute(
                text("SELECT seq, hash FROM inventory_audit_chain_head WHERE id = 1")
            ).one() == (0, "0" * 64)
            assert connection.execute(
                text("SELECT seq, hash FROM notification_audit_chain_head WHERE id = 1")
            ).one() == (0, "0" * 64)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert len(set(inspect(connection).get_table_names()) - {"alembic_version"}) == 74
        command.downgrade(config, "0009_inventory_counter")
        with engine.connect() as connection:
            names = set(inspect(connection).get_table_names()) - {"alembic_version"}
            assert len(names) == 62
            assert not names.intersection(SERVICE_SPLIT_TABLES)
    finally:
        engine.dispose()


def test_configured_upgrade_co_locates_notification_on_fresh_main_twice(tmp_path: Path) -> None:
    """A fresh main baseline receives only notification-owned additions and one stamp."""
    main_path: Path = tmp_path / "main.db"
    inventory_path: Path = tmp_path / "inventory.db"
    main_url: str = f"sqlite+aiosqlite:///{main_path}"
    inventory_url: str = f"sqlite+aiosqlite:///{inventory_path}"
    upgrade_store(main_url, Store.MAIN)
    upgrade_store(inventory_url, Store.INVENTORY)
    upgrade_configured(main_url, inventory_url)
    upgrade_configured(main_url, inventory_url)
    engine: Engine = create_engine(f"sqlite:///{main_path}")
    inventory_engine: Engine = create_engine(f"sqlite:///{inventory_path}")
    try:
        with engine.connect() as connection:
            names: set[str] = set(inspect(connection).get_table_names())
            assert names >= RETAINED_NOTIFICATION_TABLES | NOTIFICATION_ONLY_TABLES
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version_notification"))
                == "notification_0001"
            )
            assert connection.execute(
                text("SELECT id, seq, hash FROM notification_audit_chain_head")
            ).one() == (1, 0, "0" * 64)
        with inventory_engine.connect() as connection:
            assert not RETAINED_NOTIFICATION_TABLES.intersection(
                inspect(connection).get_table_names()
            )
    finally:
        engine.dispose()
        inventory_engine.dispose()


def test_configured_upgrade_adopts_legacy_notification_history_twice(tmp_path: Path) -> None:
    """A legacy 0010 main schema is validated and stamped without recreating tables."""
    main_path: Path = tmp_path / "legacy-main.db"
    inventory_path: Path = tmp_path / "inventory.db"
    main_url: str = f"sqlite+aiosqlite:///{main_path}"
    inventory_url: str = f"sqlite+aiosqlite:///{inventory_path}"
    upgrade(main_url)
    adopt_owner(main_url, Store.MAIN, writers_stopped=True)
    upgrade_configured(main_url, inventory_url)
    upgrade_configured(main_url, inventory_url)
    engine: Engine = create_engine(f"sqlite:///{main_path}")
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version_notification"))
                == "notification_0001"
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM notification_audit_chain_head")) == 1
            )
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0010_service_split"
            )
    finally:
        engine.dispose()


def test_configured_upgrade_rejects_partial_colocated_notification_without_stamp(
    tmp_path: Path,
) -> None:
    """A partial five-table addition fails before notification history is stamped."""
    main_path: Path = tmp_path / "partial-main.db"
    main_url: str = f"sqlite+aiosqlite:///{main_path}"
    upgrade_store(main_url, Store.MAIN)
    engine: Engine = create_engine(f"sqlite:///{main_path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE notification_inbox (id VARCHAR(36) PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match="co-located notification schema state"):
            upgrade_configured(main_url, f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}")
        with engine.connect() as connection:
            names: set[str] = set(inspect(connection).get_table_names())
            assert "alembic_version_notification" not in names
            assert not (NOTIFICATION_ONLY_TABLES - {"notification_inbox"}).intersection(names)
    finally:
        engine.dispose()


def test_configured_upgrade_rejects_colocated_schema_mismatch_before_writes(
    tmp_path: Path,
) -> None:
    """Retained-table drift is detected before notification-only DDL or a stamp."""
    main_path: Path = tmp_path / "mismatch-main.db"
    main_url: str = f"sqlite+aiosqlite:///{main_path}"
    upgrade_store(main_url, Store.MAIN)
    engine: Engine = create_engine(f"sqlite:///{main_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE notification_settings ADD COLUMN drift_probe INTEGER")
            )
        with pytest.raises(RuntimeError, match="owner schema columns differ"):
            upgrade_configured(main_url, f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}")
        with engine.connect() as connection:
            names: set[str] = set(inspect(connection).get_table_names())
            assert "alembic_version_notification" not in names
            assert not NOTIFICATION_ONLY_TABLES.intersection(names)
    finally:
        engine.dispose()


def test_distinct_notification_url_keeps_empty_store_requirement(tmp_path: Path) -> None:
    """An explicit separate notification store does not use co-located adoption."""
    main_url: str = f"sqlite+aiosqlite:///{tmp_path / 'main.db'}"
    inventory_url: str = f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}"
    notification_url: str = f"sqlite+aiosqlite:///{tmp_path / 'notification.db'}"
    upgrade_store(notification_url, Store.MAIN)
    with pytest.raises(RuntimeError, match="owner baseline requires an empty store"):
        upgrade_configured(main_url, inventory_url, notification_database_url=notification_url)


def test_colocated_notification_downgrade_keeps_retained_tables(tmp_path: Path) -> None:
    """Downgrading co-located notification history leaves the four main tables intact."""
    main_path: Path = tmp_path / "downgrade-main.db"
    main_url: str = f"sqlite+aiosqlite:///{main_path}"
    upgrade_store(main_url, Store.MAIN)
    upgrade_configured(main_url, f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}")
    command.downgrade(owner_config(main_url, Store.NOTIFICATION), "base")
    engine: Engine = create_engine(f"sqlite:///{main_path}")
    try:
        with engine.connect() as connection:
            names: set[str] = set(inspect(connection).get_table_names())
            assert names >= RETAINED_NOTIFICATION_TABLES
            assert not NOTIFICATION_ONLY_TABLES.intersection(names)
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version_main"))
                == "main_0002"
            )
    finally:
        engine.dispose()
