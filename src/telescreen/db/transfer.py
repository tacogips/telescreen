"""Stopped-writer inventory transfer, manifest verification and authoritative rollback."""

from contextlib import aclosing
from dataclasses import dataclass

from sqlalchemy import MetaData, delete, insert, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from telescreen.core.errors import ConfigError
from telescreen.db.owned_migrations.baseline import snapshot_tables
from telescreen.db.schema_check import baseline_metadata
from telescreen.db.transfer_check import (
    location,
    require_lineage,
    validate_inventory,
    validate_urls,
)
from telescreen.db.transfer_rows import TableDigest, TransferRow, database_cell, digest, rows


@dataclass(frozen=True)
class TransferReport:
    """Stable content proof without URLs, credentials or business values."""

    schema_revision: str
    copied: bool
    tables: tuple[TableDigest, ...]


async def manifest(connection: AsyncConnection, metadata: MetaData) -> tuple[TableDigest, ...]:
    """Cover every frozen inventory-owned table, including handoff and allocation state."""
    names: tuple[str, ...] = snapshot_tables("inventory_0001", connection.dialect.name)
    return tuple([await digest(connection, metadata.tables[name]) for name in sorted(names)])


async def _replace(source: AsyncConnection, target: AsyncConnection, metadata: MetaData) -> None:
    """Replace inventory data in one target transaction; restore trigger policy before commit."""
    names: tuple[str, ...] = snapshot_tables("inventory_0001", target.dialect.name)
    if target.dialect.name == "postgresql":
        await target.execute(
            text("ALTER TABLE data_asset_events DISABLE TRIGGER data_asset_events_append_only")
        )
    for name in reversed(names):
        await target.execute(delete(metadata.tables[name]))
    for name in names:
        batch: list[TransferRow] = []
        async with aclosing(rows(source, metadata.tables[name])) as records:
            async for row in records:
                batch.append(row)
                if len(batch) == 500:
                    await target.execute(
                        insert(metadata.tables[name]),
                        [
                            {
                                column.name: database_cell(item._mapping[column.name])
                                for column in metadata.tables[name].columns
                            }
                            for item in batch
                        ],
                    )
                    batch.clear()
        if batch:
            await target.execute(
                insert(metadata.tables[name]),
                [
                    {
                        column.name: database_cell(item._mapping[column.name])
                        for column in metadata.tables[name].columns
                    }
                    for item in batch
                ],
            )
    if target.dialect.name == "postgresql":
        await target.execute(
            text("ALTER TABLE data_asset_events ENABLE TRIGGER data_asset_events_append_only")
        )


async def _transfer(
    source: AsyncConnection, target: AsyncConnection, *, replace: bool, verify_only: bool
) -> TransferReport:
    """Hold stable source/target snapshots and commit only a fully verified target."""
    if await location(source) == await location(target):
        raise ConfigError("inventory transfer requires distinct stores")
    await source.rollback()
    await target.rollback()
    async with source.begin(), target.begin():
        for connection in (source, target):
            if connection.dialect.name == "sqlite":
                await connection.exec_driver_sql("BEGIN IMMEDIATE")
            await connection.run_sync(validate_inventory)
        metadata: MetaData = baseline_metadata()
        expected: tuple[TableDigest, ...] = await manifest(source, metadata)
        before: tuple[TableDigest, ...] = await manifest(target, metadata)
        if expected == before:
            return TransferReport("inventory_0001", False, expected)
        if verify_only:
            raise RuntimeError("inventory store manifests differ")
        if not replace and any(
            table.rows for table in before if table.table != "inventory_counters"
        ):
            raise RuntimeError(
                "inventory copy requires an empty target or an identical completed copy"
            )
        await require_lineage(source, target, metadata)
        await _replace(source, target, metadata)
        if await manifest(target, metadata) != expected:
            raise RuntimeError(
                "inventory transfer verification failed; target transaction rolled back"
            )
        await target.run_sync(validate_inventory)
        return TransferReport("inventory_0001", True, expected)


async def transfer_inventory(
    source_url: str,
    target_url: str,
    *,
    writers_stopped: bool = False,
    replace: bool = False,
    verify_only: bool = False,
) -> TransferReport:
    """Copy/verify only after every old/new API, worker and other writer has stopped.

    Default copy permits an empty target or a fully identical retry. Authoritative
    rollback uses replace=True and additionally verifies immutable target lineage.
    The source is unchanged; only the target transaction writes business data.
    """
    if not writers_stopped:
        raise ConfigError("inventory transfer requires --writers-stopped")
    validate_urls(source_url, target_url)
    source_engine: AsyncEngine = create_async_engine(source_url, poolclass=NullPool)
    target_engine: AsyncEngine = create_async_engine(target_url, poolclass=NullPool)
    try:
        async with source_engine.connect() as source, target_engine.connect() as target:
            return await _transfer(source, target, replace=replace, verify_only=verify_only)
    finally:
        try:
            await target_engine.dispose()
        finally:
            await source_engine.dispose()
