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
    """Require transfer-v2 source or target history and safe allocation state."""
    lock_owner_tables(connection, Store.INVENTORY)
    names: set[str] = set(inspect(connection).get_table_names())
    if "alembic_version_inventory" in names:
        owner: str | None = connection.execute(
            text("SELECT version_num FROM alembic_version_inventory")
        ).scalar_one_or_none()
        if owner != "inventory_0002":
            raise RuntimeError("inventory transfer requires owner revision inventory_0002")
    elif (
        "alembic_version" not in names
        or connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        != "0010_service_split"
    ):
        raise RuntimeError("inventory transfer requires legacy revision 0010_service_split")
    required: set[str] = {
        "inventory_audit_records",
        "inventory_audit_chain_head",
        "inventory_directory_entries",
        "inventory_issue_links",
        "inventory_notification_outbox",
    }
    if not required <= names:
        raise RuntimeError("inventory transfer requires inventory revision 0002 tables")
    validate_owner_schema(connection, Store.INVENTORY)
    validate_owner_state(connection, Store.INVENTORY)


async def refuse_pending_notifications(connection: AsyncConnection) -> None:
    """Require queued inventory notifications to be delivered before either transfer side."""
    pending: int = await connection.scalar(
        text("SELECT count(*) FROM inventory_notification_outbox WHERE delivered_at IS NULL")
    )
    if pending:
        raise RuntimeError("inventory transfer requires delivered notification outbox")


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
    await require_subset(
        source,
        target,
        metadata.tables["inventory_audit_records"],
        tuple(metadata.tables["inventory_audit_records"].c.keys()),
    )
    source_head: tuple[int, str] = (
        (
            await source.execute(
                text("SELECT seq, hash FROM inventory_audit_chain_head WHERE id = 1")
            )
        )
        .tuples()
        .one()
    )
    target_head: tuple[int, str] = (
        (
            await target.execute(
                text("SELECT seq, hash FROM inventory_audit_chain_head WHERE id = 1")
            )
        )
        .tuples()
        .one()
    )
    if target_head[0] > source_head[0]:
        raise RuntimeError("inventory source would rewind audit head")
    if target_head[0] == 0:
        if target_head[1] != "0" * 64:
            raise RuntimeError("inventory audit head conflicts with source lineage")
    else:
        source_hash: str | None = await source.scalar(
            text("SELECT hash FROM inventory_audit_records WHERE seq = :seq"),
            {"seq": target_head[0]},
        )
        if source_hash != target_head[1]:
            raise RuntimeError("inventory audit head conflicts with source lineage")
