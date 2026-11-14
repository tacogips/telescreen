"""Programmatic Alembic upgrade entry point."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from telescreen.db.ownership import Store


def upgrade(database_url: str, revision: str = "head") -> None:
    """Upgrade the configured database to the requested Alembic revision."""
    migration_directory: Path = Path(__file__).parent / "migrations"
    config: Config = Config()
    config.set_main_option("script_location", migration_directory.as_posix())
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, revision)


def owner_config(database_url: str, store: Store) -> Config:
    """Select one frozen migration history and its independent version table."""
    directory: Path = Path(__file__).parent / "owned_migrations"
    config: Config = Config()
    config.set_main_option("script_location", directory.as_posix())
    config.set_main_option("path_separator", "os")
    config.set_main_option("version_locations", (directory / "versions" / store.value).as_posix())
    config.set_main_option("owner_version_table", f"alembic_version_{store.value}")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_store(database_url: str, store: Store, revision: str = "head") -> None:
    """Upgrade one owner without inspecting or migrating the other owner's database."""
    command.upgrade(owner_config(database_url, store), revision)


async def _has_owner_history(database_url: str) -> bool:
    """Continue adopted owner histories even when they share one physical connection URL."""
    engine: AsyncEngine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            names: set[str] = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
            for table in ("alembic_version_main", "alembic_version_inventory"):
                if (
                    table in names
                    and await connection.scalar(text(f"SELECT version_num FROM {table}"))
                    is not None
                ):
                    return True
            return False
    finally:
        await engine.dispose()


def upgrade_configured(
    database_url: str,
    inventory_database_url: str | None = None,
    revision: str = "head",
    store: Store | None = None,
) -> None:
    """Select shared legacy history or explicit owner histories from deployment settings."""
    if store is not None:
        selected_url: str = (
            inventory_database_url or database_url if store is Store.INVENTORY else database_url
        )
        upgrade_store(selected_url, store, revision)
    elif inventory_database_url is None or inventory_database_url == database_url:
        if asyncio.run(_has_owner_history(database_url)):
            upgrade_store(database_url, Store.MAIN, revision)
            upgrade_store(database_url, Store.INVENTORY, revision)
        else:
            upgrade(database_url, revision)
    else:
        upgrade_store(database_url, Store.MAIN, revision)
        upgrade_store(inventory_database_url, Store.INVENTORY, revision)
