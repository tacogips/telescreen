"""Programmatic Alembic upgrade entry point."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from telescreen.core.errors import ConfigError
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


async def _owner_history_state(database_url: str) -> tuple[bool, bool, bool]:
    """Continue adopted owner histories even when they share one physical connection URL."""
    engine: AsyncEngine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            names: set[str] = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
            main: bool = (
                "alembic_version_main" in names
                and await connection.scalar(text("SELECT version_num FROM alembic_version_main"))
                is not None
            )
            inventory: bool = (
                "alembic_version_inventory" in names
                and await connection.scalar(
                    text("SELECT version_num FROM alembic_version_inventory")
                )
                is not None
            )
            notification: bool = (
                "alembic_version_notification" in names
                and await connection.scalar(
                    text("SELECT version_num FROM alembic_version_notification")
                )
                is not None
            )
            return main, inventory, notification
    finally:
        await engine.dispose()


def upgrade_configured(
    database_url: str,
    inventory_database_url: str | None = None,
    revision: str = "head",
    store: Store | None = None,
    notification_database_url: str | None = None,
) -> None:
    """Select shared legacy history or explicit owner histories from deployment settings."""
    if store is not None:
        selected_url: str = database_url
        if store is Store.INVENTORY:
            selected_url = inventory_database_url or database_url
        elif store is Store.NOTIFICATION:
            selected_url = notification_database_url or database_url
        upgrade_store(selected_url, store, revision)
    elif (inventory_database_url is None or inventory_database_url == database_url) and (
        notification_database_url is None or notification_database_url == database_url
    ):
        owner_history: tuple[bool, bool, bool] = asyncio.run(_owner_history_state(database_url))
        main_history: bool = owner_history[0]
        inventory_history: bool = owner_history[1]
        notification_history: bool = owner_history[2]
        if main_history or inventory_history or notification_history:
            if not notification_history:
                raise ConfigError("notification history requires adoption")
            upgrade_store(database_url, Store.MAIN, revision)
            upgrade_store(database_url, Store.INVENTORY, revision)
            upgrade_store(database_url, Store.NOTIFICATION, revision)
        else:
            upgrade(database_url, revision)
    else:
        upgrade_store(database_url, Store.MAIN, revision)
        upgrade_store(inventory_database_url or database_url, Store.INVENTORY, revision)
        if notification_database_url is None or notification_database_url == database_url:
            notification_config: Config = owner_config(database_url, Store.NOTIFICATION)
            notification_config.attributes["colocated_notification"] = True
            command.upgrade(notification_config, revision)
        else:
            upgrade_store(notification_database_url, Store.NOTIFICATION, revision)
