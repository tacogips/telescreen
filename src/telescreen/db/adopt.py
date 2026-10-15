"""Explicit checked adoption of legacy owner tables without changing business data."""

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from telescreen.core.errors import ConfigError
from telescreen.db.migrate import owner_config
from telescreen.db.owned_migrations.baseline import snapshot_tables
from telescreen.db.ownership import Store
from telescreen.db.schema_check import validate_owner_schema


def validate_owner_state(connection: Connection, store: Store) -> None:
    """Require initialized allocation and audit state rather than silently reseeding it."""
    if store is Store.NOTIFICATION:
        head: tuple[int, str] | None = (
            connection.execute(
                text("SELECT seq, hash FROM notification_audit_chain_head WHERE id = 1")
            )
            .tuples()
            .one_or_none()
        )
        if head is None:
            raise RuntimeError("notification owner audit head is missing")
        return
    counter: str = "counters" if store is Store.MAIN else "inventory_counters"
    counter_name: str = "issue_number" if store is Store.MAIN else "data_asset_number"
    master: str = "issues" if store is Store.MAIN else "data_assets"
    allocated: int | None = connection.execute(
        text(f"SELECT value FROM {counter} WHERE name = :name"), {"name": counter_name}
    ).scalar_one_or_none()
    maximum: int = connection.execute(
        text(f"SELECT COALESCE(MAX(number), 0) FROM {master}")
    ).scalar_one()
    if allocated is None or allocated < max(0, maximum):
        raise RuntimeError("owner allocation state is missing or behind existing records")
    if store is Store.MAIN:
        head: tuple[int, str] | None = (
            connection.execute(text("SELECT seq, hash FROM audit_chain_head WHERE id = 1"))
            .tuples()
            .one_or_none()
        )
        last: tuple[int, str] | None = (
            connection.execute(
                text("SELECT seq, hash FROM audit_records ORDER BY seq DESC LIMIT 1")
            )
            .tuples()
            .one_or_none()
        )
        if head != (last or (0, "0" * 64)):
            raise RuntimeError("owner audit head does not match the existing chain")


def lock_owner_tables(connection: Connection, store: Store) -> None:
    """Exclude writes while validating and stamping PostgreSQL owner tables."""
    if connection.dialect.name == "postgresql":
        names: tuple[str, ...] = snapshot_tables(f"{store}_0001", "postgresql")
        existing: set[str] = set(inspect(connection).get_table_names())
        if not set(names) <= existing:
            raise RuntimeError("owner schema is missing baseline tables")
        names = names + tuple(
            name for name in (f"alembic_version_{store}", "alembic_version") if name in existing
        )
        quoted: str = ", ".join(f'"{name}"' for name in sorted(names))
        connection.execute(text(f"LOCK TABLE {quoted} IN SHARE ROW EXCLUSIVE MODE"))


def adopt_connection(connection: Connection, database_url: str, store: Store) -> None:
    """Validate and stamp on the caller's transaction; never recreate existing tables."""
    lock_owner_tables(connection, store)
    names: set[str] = set(inspect(connection).get_table_names())
    version_table: str = f"alembic_version_{store}"
    if version_table in names:
        version: str | None = connection.execute(
            text(f"SELECT version_num FROM {version_table}")
        ).scalar_one_or_none()
        if version not in (None, f"{store}_0001"):
            raise RuntimeError("owner adoption requires the baseline revision")
    else:
        version = None
    if version is None and (
        "alembic_version" not in names
        or connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        != "0009_inventory_counter"
    ):
        raise RuntimeError("owner adoption requires legacy revision 0009_inventory_counter")
    validate_owner_schema(connection, store)
    validate_owner_state(connection, store)
    config: Config = owner_config(database_url, store)
    config.attributes["connection"] = connection
    command.stamp(config, f"{store}_0001")


async def _adopt(database_url: str, store: Store) -> None:
    """Include validation and version-table DDL in one owner transaction."""
    engine: AsyncEngine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            if connection.dialect.name == "sqlite":
                await connection.exec_driver_sql("BEGIN IMMEDIATE")
            await connection.run_sync(adopt_connection, database_url, store)
    finally:
        await engine.dispose()


def adopt_owner(database_url: str, store: Store, *, writers_stopped: bool = False) -> None:
    """Adopt only after the operator stops API, workers and every other application writer."""
    if not writers_stopped:
        raise ConfigError("owner adoption requires --writers-stopped")
    asyncio.run(_adopt(database_url, store))
