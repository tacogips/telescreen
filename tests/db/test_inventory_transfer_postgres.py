"""Owner-role transfer and rollback restore PostgreSQL append-only policy atomically."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Table, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from telescreen.db import transfer
from telescreen.db.migrate import owner_config, upgrade, upgrade_store
from telescreen.db.ownership import Store
from telescreen.db.transfer import TransferReport, transfer_inventory
from telescreen.db.transfer_rows import TableDigest, TransferRow
from tests.db.test_inventory_transfer import contents
from tests.db.transfer_support import seed_inventory
from tests.support.owned_postgres import OwnedPostgres, owned_postgres

__all__: list[str] = ["owned_postgres"]
pytestmark: list[pytest.MarkDecorator] = [pytest.mark.asyncio, pytest.mark.integration]


async def test_pg_transfer_failure_retry_and_rollback_under_owner_credentials(
    owned_postgres: OwnedPostgres, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real mid-copy failure rolls back partial inserts and the disabled event trigger."""
    await asyncio.to_thread(
        command.downgrade, owner_config(owned_postgres.main_url, Store.MAIN), "base"
    )
    await asyncio.to_thread(upgrade, owned_postgres.main_url)
    await seed_inventory(owned_postgres.main)
    before: tuple[TableDigest, ...] = await contents(owned_postgres.inventory)
    original: Callable[[AsyncConnection, Table], AsyncGenerator[TransferRow, None]] = transfer.rows

    async def fail(connection: AsyncConnection, table: Table) -> AsyncGenerator[TransferRow, None]:
        if table.name == "data_assets":
            raise RuntimeError("mid-copy interruption")
        async for row in original(connection, table):
            yield row

    with monkeypatch.context() as patch:
        patch.setattr(transfer, "rows", fail)
        with pytest.raises(RuntimeError, match="mid-copy interruption"):
            await transfer_inventory(
                owned_postgres.main_url, owned_postgres.inventory_url, writers_stopped=True
            )
    assert await contents(owned_postgres.inventory) == before
    report: TransferReport = await transfer_inventory(
        owned_postgres.main_url, owned_postgres.inventory_url, writers_stopped=True
    )
    assert report.copied
    assert report.tables == await contents(owned_postgres.main)
    async with owned_postgres.inventory.begin() as connection:
        await connection.execute(text("UPDATE data_assets SET name = 'New master'"))
        await connection.execute(text("UPDATE inventory_counters SET value = 39"))
    report = await transfer_inventory(
        owned_postgres.inventory_url,
        owned_postgres.main_url,
        writers_stopped=True,
        replace=True,
    )
    assert report.copied
    assert report.tables == await contents(owned_postgres.inventory)
    for engine in (owned_postgres.main, owned_postgres.inventory):
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT tgenabled::text FROM pg_trigger "
                        "WHERE tgrelid = 'data_asset_events'::regclass "
                        "AND tgname = 'data_asset_events_append_only'"
                    )
                )
                == "O"
            )
        async with engine.begin() as connection:
            with pytest.raises(SQLAlchemyError, match="append-only"):
                await connection.execute(text("UPDATE data_asset_events SET note = 'forbidden'"))


async def test_sqlite_postgres_roundtrip_preserves_manifests_and_utc_timestamps(
    tmp_path: Path, owned_postgres: OwnedPostgres
) -> None:
    """Different SQL types, collation and timezone representation cannot change transfer data."""
    url: str = f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}"
    await asyncio.to_thread(upgrade_store, url, Store.INVENTORY)
    sqlite: AsyncEngine = create_async_engine(url)
    try:
        await seed_inventory(sqlite)
        original: tuple[TableDigest, ...] = await contents(sqlite)
        report: TransferReport = await transfer_inventory(
            url, owned_postgres.inventory_url, writers_stopped=True
        )
        assert report.copied and report.tables == original
        assert await contents(owned_postgres.inventory) == original
        async with owned_postgres.inventory.begin() as connection:
            await connection.execute(text("UPDATE data_assets SET name = 'Edited after split'"))
        report = await transfer_inventory(
            owned_postgres.inventory_url, url, writers_stopped=True, replace=True
        )
        assert report.copied
        assert await contents(sqlite) == await contents(owned_postgres.inventory)
    finally:
        await sqlite.dispose()
