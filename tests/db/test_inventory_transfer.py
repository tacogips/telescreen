"""Atomic inventory copy, retry, manifest comparison and rollback after new writes."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from telescreen.core.errors import ConfigError
from telescreen.db import transfer
from telescreen.db.migrate import upgrade, upgrade_store
from telescreen.db.models import (
    DataAssetEventRow,
    DataAssetRow,
    DataFieldValueRow,
    InventoryInboxRow,
)
from telescreen.db.ownership import Store
from telescreen.db.schema_check import baseline_metadata
from telescreen.db.session import create_sessionmaker
from telescreen.db.transfer import TransferReport, manifest, transfer_inventory
from telescreen.db.transfer_rows import TableDigest
from tests.db.transfer_support import NOW, seed_inventory
from tests.inventory.retention.helpers import _asset


@dataclass(frozen=True)
class TransferWorld:
    """A legacy shared source and an empty independently migrated inventory target."""

    source: AsyncEngine
    target: AsyncEngine
    source_url: str = field(repr=False)
    target_url: str = field(repr=False)

    async def copy(self, *, merge: bool = False, verify: bool = False) -> TransferReport:
        """Invoke the real transfer with the stopped-writer precondition."""
        return await transfer_inventory(
            self.target_url if merge else self.source_url,
            self.source_url if merge else self.target_url,
            writers_stopped=True,
            replace=merge,
            verify_only=verify,
        )


@pytest_asyncio.fixture
async def world(tmp_path: Path) -> AsyncIterator[TransferWorld]:
    """Never use ORM create_all to stand in for either migration history."""
    source_url: str = f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}"
    target_url: str = f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}"
    await asyncio.to_thread(upgrade, source_url)
    await asyncio.to_thread(upgrade_store, target_url, Store.INVENTORY)
    source: AsyncEngine = create_async_engine(source_url)
    target: AsyncEngine = create_async_engine(target_url)
    try:
        await seed_inventory(source)
        async with source.begin() as connection:
            await connection.execute(
                text("UPDATE counters SET value = 73 WHERE name = 'issue_number'")
            )
        yield TransferWorld(source, target, source_url, target_url)
    finally:
        await target.dispose()
        await source.dispose()


async def contents(engine: AsyncEngine) -> tuple[TableDigest, ...]:
    """Read a deterministic snapshot for mutation/failure assertions."""
    async with engine.connect() as connection:
        return await manifest(connection, baseline_metadata())


@pytest.mark.asyncio
async def test_copy_retry_verify_and_rollback_preserve_all_owner_state(
    world: TransferWorld,
) -> None:
    """Rollback includes new assets and revisions, physical value deletion and acknowledgment."""
    before: tuple[TableDigest, ...] = await contents(world.source)
    report: TransferReport = await world.copy()
    assert report.copied and report.tables == before
    assert all(table.rows > 0 for table in report.tables)
    assert await contents(world.source) == before
    assert (await world.copy()).copied is False
    assert (await world.copy(verify=True)).tables == before
    async with create_sessionmaker(world.target).begin() as session:
        session.add(_asset("asset-new", 32, NOW))
        value: DataFieldValueRow | None = await session.get(DataFieldValueRow, ("asset-1", "due"))
        assert value is not None and value.revision_id == "original-revision"
        await session.delete(value)
        asset: DataAssetRow | None = await session.get(DataAssetRow, "asset-2")
        assert asset is not None
        asset.name = "Master edited after cutover"
    async with world.target.begin() as connection:
        await connection.execute(text("UPDATE inventory_counters SET value = 37"))
        await connection.execute(text("UPDATE inventory_inbox_streams SET last_sequence = 13"))
        await connection.execute(
            text("UPDATE inventory_audit_outbox SET delivered_at = :at"), {"at": NOW.isoformat()}
        )
    with pytest.raises(RuntimeError, match="manifests differ"):
        await world.copy(verify=True)
    updated: tuple[TableDigest, ...] = await contents(world.target)
    assert (await world.copy(merge=True)).tables == updated
    assert await contents(world.source) == updated
    assert (await world.copy(merge=True)).copied is False
    async with world.source.connect() as connection:
        assert (
            await connection.scalar(text("SELECT value FROM counters WHERE name = 'issue_number'"))
            == 73
        )
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0009_inventory_counter"
        )


@pytest.mark.asyncio
async def test_failed_copy_rolls_back_the_entire_target_and_can_retry(
    world: TransferWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after all replacement DML preserves both stores and permits an ordinary retry."""
    original: Callable[[AsyncConnection, AsyncConnection, MetaData], Awaitable[None]] = (
        transfer._replace
    )
    before: tuple[TableDigest, ...] = await contents(world.target)

    async def fail(source: AsyncConnection, target: AsyncConnection, metadata: MetaData) -> None:
        await original(source, target, metadata)
        raise RuntimeError("copy interrupted before commit")

    with monkeypatch.context() as patch:
        patch.setattr(transfer, "_replace", fail)
        with pytest.raises(RuntimeError, match="copy interrupted"):
            await world.copy()
    assert await contents(world.target) == before
    assert (await world.copy()).copied


@pytest.mark.asyncio
async def test_target_manifest_mismatch_rolls_back_replacement(
    world: TransferWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verification checks copied contents, so a corrupted replacement cannot commit."""
    original: Callable[[AsyncConnection, AsyncConnection, MetaData], Awaitable[None]] = (
        transfer._replace
    )
    before: tuple[TableDigest, ...] = await contents(world.target)

    async def corrupt(source: AsyncConnection, target: AsyncConnection, metadata: MetaData) -> None:
        await original(source, target, metadata)
        await target.execute(text("UPDATE data_assets SET name = 'corrupted copy'"))

    with monkeypatch.context() as patch:
        patch.setattr(transfer, "_replace", corrupt)
        with pytest.raises(RuntimeError, match="verification failed"):
            await world.copy()
    assert await contents(world.target) == before


@pytest.mark.asyncio
async def test_rollback_does_not_rewind_a_committed_outbox_acknowledgment(
    world: TransferWorld,
) -> None:
    """An otherwise matching source must retain target delivery progress."""
    await world.copy()
    async with world.source.begin() as connection:
        await connection.execute(
            text("UPDATE inventory_audit_outbox SET delivered_at = :at"), {"at": NOW.isoformat()}
        )
    before: tuple[TableDigest, ...] = await contents(world.source)
    with pytest.raises(RuntimeError, match="rewind acknowledgment"):
        await world.copy(merge=True)
    assert await contents(world.source) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM data_asset_events",
        "UPDATE data_asset_events SET note = 'rewritten'",
        "UPDATE data_assets SET resource_key = 'changed' WHERE id = 'asset-1'",
        "UPDATE inventory_counters SET value = 30",
        "UPDATE inventory_inbox_streams SET last_sequence = 11",
        "DELETE FROM inventory_inbox WHERE sequence = 10",
        "DELETE FROM inventory_audit_outbox",
    ],
)
async def test_rollback_refuses_missing_or_rewritten_history(
    world: TransferWorld, mutation: str
) -> None:
    """An older/conflicting source cannot overwrite a target's known durable lineage."""
    await world.copy()
    async with world.target.begin() as connection:
        await connection.execute(text(mutation))
    before: tuple[TableDigest, ...] = await contents(world.source)
    with pytest.raises(RuntimeError, match="target history|rewind progress"):
        await world.copy(merge=True)
    assert await contents(world.source) == before


@pytest.mark.asyncio
async def test_copy_refuses_different_nonempty_target(world: TransferWorld) -> None:
    """A repeated initial copy never silently replaces new target metadata."""
    await world.copy()
    async with world.target.begin() as connection:
        await connection.execute(text("UPDATE data_assets SET name = 'New master'"))
    before: tuple[TableDigest, ...] = await contents(world.target)
    with pytest.raises(RuntimeError, match="empty target"):
        await world.copy()
    assert await contents(world.target) == before


@pytest.mark.asyncio
async def test_transfer_preconditions_are_enforced_before_opening_stores(
    world: TransferWorld,
) -> None:
    """Explicit quiescence and distinct persistent stores are required."""
    with pytest.raises(ConfigError, match="writers-stopped"):
        await transfer_inventory(world.source_url, world.target_url)
    with pytest.raises(ConfigError, match="distinct"):
        await transfer_inventory(world.source_url, world.source_url, writers_stopped=True)
    with pytest.raises(ConfigError, match="persistent"):
        await transfer_inventory(
            "sqlite+aiosqlite:///:memory:", world.target_url, writers_stopped=True
        )


@pytest.mark.asyncio
async def test_keyset_pages_preserve_every_row_and_composite_numeric_key(
    world: TransferWorld,
) -> None:
    """Multiple read/write batches and binary text ordering cover a complete rollback traversal."""
    async with create_sessionmaker(world.source).begin() as session:
        for sequence in range(13, 1018):
            session.add(
                InventoryInboxRow(
                    id=f"receipt-{sequence}",
                    stream_id="collector:copy",
                    sequence=sequence,
                    payload_sha256=f"{sequence:064x}",
                    applied_at=NOW,
                )
            )
            session.add(
                DataAssetEventRow(
                    id=f"event-{'Z' if sequence % 2 else 'é'}-{sequence}",
                    asset_id="asset-1",
                    kind="observed",
                    recorded_at=NOW,
                    actor_ref="system",
                )
            )
    async with world.source.begin() as connection:
        await connection.execute(text("UPDATE inventory_inbox_streams SET last_sequence = 1017"))
    report: TransferReport = await world.copy()
    assert next(table.rows for table in report.tables if table.table == "inventory_inbox") == 1017
    assert report.tables == await contents(world.target)
    async with world.target.begin() as connection:
        await connection.execute(text("UPDATE data_assets SET name = 'New master'"))
    assert (await world.copy(merge=True)).copied
    assert await contents(world.source) == await contents(world.target)
