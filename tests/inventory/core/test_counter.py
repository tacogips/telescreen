"""Inventory allocation rolls back correctly on SQLite without legacy counter tables."""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from telescreen.db.base import Base
from telescreen.db.models import InventoryCounterRow
from telescreen.db.session import create_sessionmaker
from telescreen.inventory.repository import InventoryRepository


@pytest.mark.asyncio
async def test_first_inventory_allocation_and_increment_rollback_atomically(tmp_path: Path) -> None:
    """Neither a first insert nor later increments consume numbers after rollback."""
    engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}")
    repository: InventoryRepository = InventoryRepository()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.tables["inventory_counters"].create)
        with pytest.raises(RuntimeError, match="rollback"):
            async with create_sessionmaker(engine).begin() as session:
                assert await repository.next_asset_number(session) == 1
                raise RuntimeError("rollback")
        async with create_sessionmaker(engine)() as session:
            assert await session.scalar(select(InventoryCounterRow)) is None
        async with create_sessionmaker(engine).begin() as session:
            assert await repository.next_asset_number(session) == 1
        with pytest.raises(RuntimeError, match="rollback"):
            async with create_sessionmaker(engine).begin() as session:
                assert await repository.next_asset_number(session) == 2
                raise RuntimeError("rollback")
        async with create_sessionmaker(engine).begin() as session:
            assert await repository.next_asset_number(session) == 2
    finally:
        await engine.dispose()
