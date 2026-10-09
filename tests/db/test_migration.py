"""Alembic migration parity and PostgreSQL trigger tests."""

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, insert, inspect, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from telescreen.db.base import Base
from telescreen.db.migrate import upgrade
from telescreen.db.models import AuditRecordRow


@pytest.mark.asyncio
async def test_sqlite_migration_matches_model_tables(tmp_path: Path) -> None:
    """Create a file-backed database and compare migrated columns with metadata."""
    database_file: Path = tmp_path / "migration.sqlite"
    database_url: str = f"sqlite+aiosqlite:///{database_file}"
    await asyncio.to_thread(upgrade, database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    async with engine.connect() as connection:
        migrated: set[tuple[str, tuple[str, ...]]] = await connection.run_sync(
            lambda sync_connection: {
                (
                    table_name,
                    tuple(
                        column["name"]
                        for column in inspect(sync_connection).get_columns(table_name)
                    ),
                )
                for table_name in inspect(sync_connection).get_table_names()
                if table_name != "alembic_version"
            }
        )
    expected: set[tuple[str, tuple[str, ...]]] = {
        (table_name, tuple(column.name for column in table.columns))
        for table_name, table in Base.metadata.tables.items()
    }
    assert migrated == expected
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_audit_records_reject_update_and_delete() -> None:
    """Install and exercise the PostgreSQL append-only audit trigger."""
    database_url: str | None = os.environ.get("TELESCREEN_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("requires TELESCREEN_TEST_DATABASE_URL")
    await asyncio.to_thread(upgrade, database_url)
    async_engine: AsyncEngine = create_async_engine(database_url)
    sequence: int = 1_000_000_000 + time.time_ns() % 1_000_000_000
    observed_at: datetime = datetime.now(UTC)
    async with async_engine.begin() as connection:
        await connection.execute(
            insert(AuditRecordRow).values(
                seq=sequence,
                id=f"integration-audit-{sequence}",
                at=observed_at,
                actor_kind="system",
                actor_ref="integration",
                action="test",
                target_kind="test",
                target_id="test",
                summary="masked test entry",
                details_json="{}",
                prev_hash="0" * 64,
                hash="1" * 64,
            )
        )
    async with async_engine.begin() as connection:
        with pytest.raises(SQLAlchemyError, match="append-only"):
            await connection.execute(
                update(AuditRecordRow)
                .where(AuditRecordRow.seq == sequence)
                .values(summary="updated")
            )
    async with async_engine.begin() as connection:
        with pytest.raises(SQLAlchemyError, match="append-only"):
            await connection.execute(delete(AuditRecordRow).where(AuditRecordRow.seq == sequence))
    await async_engine.dispose()
