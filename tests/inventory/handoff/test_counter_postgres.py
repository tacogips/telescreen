"""Atomic inventory numbering works on an empty owner-local table under contention."""

import asyncio

import pytest
from sqlalchemy import select

from telescreen.db.models import InventoryCounterRow
from telescreen.inventory.repository import InventoryRepository
from tests.inventory.handoff.pg_fixture import PgHandoff, pg_handoff

__all__: list[str] = ["pg_handoff"]
pytestmark: list[pytest.MarkDecorator] = [pytest.mark.asyncio, pytest.mark.integration]


async def test_concurrent_first_allocations_and_rollback_are_inventory_local(
    pg_handoff: PgHandoff,
) -> None:
    """Concurrent first use allocates unique numbers; a rollback does not consume one."""
    repository: InventoryRepository = InventoryRepository()

    async def allocate() -> int:
        async with pg_handoff.world.inventory.begin() as session:
            return await repository.next_asset_number(session)

    numbers: tuple[int, ...] = tuple(await asyncio.gather(*(allocate() for _ in range(24))))
    assert sorted(numbers) == list(range(1, 25))
    with pytest.raises(RuntimeError, match="rollback"):
        async with pg_handoff.world.inventory.begin() as session:
            assert await repository.next_asset_number(session) == 25
            raise RuntimeError("rollback")
    assert await allocate() == 25
    async with pg_handoff.world.inventory() as session:
        assert await session.scalar(select(InventoryCounterRow.value)) == 25
