"""Offline transfer preconditions and immutable inventory lineage checks."""

from pathlib import Path

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from telescreen.core.errors import ConfigError
from telescreen.db.adopt import lock_owner_tables, validate_owner_state
from telescreen.db.ownership import Store
from telescreen.db.schema_check import validate_owner_schema
from telescreen.db.transfer_rows import require_subset


def validate_urls(source: str, target: str) -> None:
    """Refuse an obvious self-copy and ephemeral SQLite engine-local databases."""
    source_url: URL = make_url(source)
    target_url: URL = make_url(target)
    for url in (source_url, target_url):
        if url.get_backend_name() not in ("sqlite", "postgresql"):
            raise ConfigError("inventory transfer supports SQLite and PostgreSQL")
        if url.get_backend_name() == "sqlite" and url.database in (None, "", ":memory:"):
            raise ConfigError("inventory transfer requires persistent stores")
    if source_url == target_url or (
        source_url.get_backend_name() == target_url.get_backend_name() == "sqlite"
        and Path(source_url.database or "").resolve() == Path(target_url.database or "").resolve()
    ):
        raise ConfigError("inventory transfer requires distinct stores")


async def location(connection: AsyncConnection) -> tuple[str, ...]:
    """Detect the same physical store before acquiring conflicting transaction locks."""
    if connection.dialect.name == "sqlite":
        databases: tuple[int, str, str] = (
            (await connection.exec_driver_sql("PRAGMA database_list")).tuples().one()
        )
        return ("sqlite", str(Path(databases[2]).resolve()))
    identity: tuple[str, str, str] = (
        (
            await connection.execute(
                text(
                    "SELECT current_database(), current_schema(), pg_postmaster_start_time()::text"
                )
            )
        )
        .tuples()
        .one()
    )
    return ("postgresql", *identity)


def validate_inventory(connection: Connection) -> None:
    """Require a known migration state, exact baseline schema and safe allocation state."""
    lock_owner_tables(connection, Store.INVENTORY)
    names: set[str] = set(inspect(connection).get_table_names())
    if "alembic_version_inventory" in names:
        owner: str | None = connection.execute(
            text("SELECT version_num FROM alembic_version_inventory")
        ).scalar_one_or_none()
        if owner != "inventory_0001":
            raise RuntimeError("inventory transfer requires owner baseline inventory_0001")
    elif (
        "alembic_version" not in names
        or connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        != "0009_inventory_counter"
    ):
        raise RuntimeError("inventory transfer requires legacy revision 0009_inventory_counter")
    validate_owner_schema(connection, Store.INVENTORY)
    validate_owner_state(connection, Store.INVENTORY)


async def require_lineage(
    source: AsyncConnection, target: AsyncConnection, metadata: MetaData
) -> None:
    """An authoritative rollback cannot forget old assets, commands, intents or events."""
    await require_subset(
        source, target, metadata.tables["data_assets"], ("id", "number", "resource_key")
    )
    for name in ("data_asset_events", "inventory_inbox"):
        await require_subset(
            source, target, metadata.tables[name], tuple(metadata.tables[name].c.keys())
        )
    await require_subset(
        source, target, metadata.tables["inventory_counters"], ("name",), monotonic="value"
    )
    await require_subset(
        source,
        target,
        metadata.tables["inventory_inbox_streams"],
        ("id",),
        monotonic="last_sequence",
    )
    for name in ("inventory_audit_outbox", "inventory_retention_outbox"):
        await require_subset(
            source,
            target,
            metadata.tables[name],
            ("id", "payload_json", "recorded_at"),
            acknowledged=True,
        )
