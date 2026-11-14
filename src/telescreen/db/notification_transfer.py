"""Stopped-writer transfer and value-free verification for notification-owned data."""

from contextlib import aclosing
from dataclasses import dataclass

from sqlalchemy import MetaData, Table, delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from telescreen.core.errors import ConfigError
from telescreen.db.base import Base
from telescreen.db.transfer_check import location, validate_urls
from telescreen.db.transfer_rows import (
    TableDigest,
    TransferRow,
    database_cell,
    digest,
    require_subset,
    rows,
)

_RETAINED: tuple[str, ...] = (
    "notifications",
    "notification_templates",
    "notification_settings",
    "retention_notification_receipts",
)
_AUDIT: tuple[str, ...] = ("notification_audit_records", "notification_audit_chain_head")


@dataclass(frozen=True)
class NotificationTransferReport:
    """Transfer result containing counts and hashes but no business values."""

    schema_revision: str
    copied: bool
    tables: tuple[TableDigest, ...]


def _names(merge: bool) -> tuple[str, ...]:
    """Select retained tables for split and the audit chain for merge."""
    return _RETAINED + _AUDIT if merge else _RETAINED


async def _manifest(
    connection: AsyncConnection, metadata: MetaData, merge: bool
) -> tuple[TableDigest, ...]:
    """Hash selected tables in stable order without exposing row contents."""
    return tuple(
        [await digest(connection, metadata.tables[name]) for name in sorted(_names(merge))]
    )


async def _copy_table(source: AsyncConnection, target: AsyncConnection, table_name: str) -> None:
    """Replace one target table in bounded batches."""
    table: Table = Base.metadata.tables[table_name]
    await target.execute(delete(table))
    batch: list[TransferRow] = []
    async with aclosing(rows(source, table)) as source_rows:
        async for row in source_rows:
            batch.append(row)
            if len(batch) == 500:
                await target.execute(
                    insert(table),
                    [
                        {
                            column.name: database_cell(item._mapping[column.name])
                            for column in table.columns
                        }
                        for item in batch
                    ],
                )
                batch.clear()
    if batch:
        await target.execute(
            insert(table),
            [
                {column.name: database_cell(item._mapping[column.name]) for column in table.columns}
                for item in batch
            ],
        )


async def _merge_subset(
    source: AsyncConnection, target: AsyncConnection, metadata: MetaData
) -> None:
    """Require target receipts, templates and audit history to exist unchanged in source."""
    for table_name in ("retention_notification_receipts", "notification_templates"):
        table: Table = metadata.tables[table_name]
        await require_subset(source, target, table, tuple(column.name for column in table.columns))
    audit: Table = metadata.tables["notification_audit_records"]
    await require_subset(source, target, audit, tuple(column.name for column in audit.columns))
    head: Table = metadata.tables["notification_audit_chain_head"]
    source_head: tuple[int, str] | None = (
        (await source.execute(select(head.c.seq, head.c.hash).where(head.c.id == 1)))
        .tuples()
        .one_or_none()
    )
    target_head: tuple[int, str] | None = (
        (await target.execute(select(head.c.seq, head.c.hash).where(head.c.id == 1)))
        .tuples()
        .one_or_none()
    )
    if source_head is None or target_head is None or target_head[0] > source_head[0]:
        raise RuntimeError("notification audit source does not include target history")
    if target_head[0] == 0:
        return
    record: Table = metadata.tables["notification_audit_records"]
    source_hash: str | None = await source.scalar(
        select(record.c.hash).where(record.c.seq == target_head[0])
    )
    if source_hash != target_head[1]:
        raise RuntimeError("notification audit source conflicts with target history")


async def _transfer(
    source: AsyncConnection,
    target: AsyncConnection,
    *,
    merge: bool,
    verify_only: bool,
) -> NotificationTransferReport:
    """Copy in stable owner transactions and roll back any partial mismatch."""
    if await location(source) == await location(target):
        raise ConfigError("notification transfer requires distinct stores")
    await source.rollback()
    await target.rollback()
    async with source.begin(), target.begin():
        for connection in (source, target):
            if connection.dialect.name == "sqlite":
                await connection.exec_driver_sql("BEGIN IMMEDIATE")
        metadata: MetaData = Base.metadata
        expected: tuple[TableDigest, ...] = await _manifest(source, metadata, merge)
        before: tuple[TableDigest, ...] = await _manifest(target, metadata, merge)
        if expected == before:
            return NotificationTransferReport("notification_0001", False, expected)
        if verify_only:
            raise RuntimeError("notification store manifests differ")
        if merge:
            await _merge_subset(source, target, metadata)
        elif any(item.rows for item in before):
            raise RuntimeError("notification copy requires an empty or identical target")
        if target.dialect.name == "postgresql" and merge:
            trigger: str = "notification_audit_records_append_only"
            await target.execute(
                text(f"ALTER TABLE notification_audit_records DISABLE TRIGGER {trigger}")
            )
        names: tuple[str, ...] = _names(merge)
        for name in reversed(names):
            await target.execute(delete(metadata.tables[name]))
        for name in names:
            await _copy_table(source, target, name)
        if target.dialect.name == "postgresql" and merge:
            await target.execute(
                text(f"ALTER TABLE notification_audit_records ENABLE TRIGGER {trigger}")
            )
        if await _manifest(target, metadata, merge) != expected:
            raise RuntimeError("notification transfer verification failed")
        return NotificationTransferReport("notification_0001", True, expected)


async def transfer_notification_store(
    source_url: str,
    target_url: str,
    *,
    writers_stopped: bool = False,
    merge: bool = False,
    verify_only: bool = False,
) -> NotificationTransferReport:
    """Transfer only while all notification and retained-table writers are stopped."""
    if not writers_stopped:
        raise ConfigError("notification transfer requires --writers-stopped")
    validate_urls(source_url, target_url)
    source_engine: AsyncEngine = create_async_engine(source_url, poolclass=NullPool)
    target_engine: AsyncEngine = create_async_engine(target_url, poolclass=NullPool)
    try:
        async with source_engine.connect() as source, target_engine.connect() as target:
            return await _transfer(source, target, merge=merge, verify_only=verify_only)
    finally:
        try:
            await target_engine.dispose()
        finally:
            await source_engine.dispose()
