"""Regression tests for the idempotent three-service schema migration."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from telescreen.db.migrate import upgrade

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
