"""PostgreSQL proves concurrent publication and receipt deduplication under separate owners."""

import asyncio

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError

from telescreen.audit.verify import verify_chain
from telescreen.db.models import (
    InventoryRetentionOutboxRow,
    NotificationRow,
    RetentionNotificationReceiptRow,
)
from telescreen.ports.retention import RetentionCommand, RetentionReceipt
from tests.inventory.handoff.pg_fixture import PgHandoff, pg_handoff
from tests.inventory.retention.conftest import RetentionWorld, make_retention_world

__all__: list[str] = ["pg_handoff"]
pytestmark: list[pytest.MarkDecorator] = [pytest.mark.asyncio, pytest.mark.integration]


async def test_concurrent_checks_and_duplicate_receivers_use_separate_database_roles(
    pg_handoff: PgHandoff,
) -> None:
    """Twelve scanners publish once, and 24 receiver attempts persist one notification intent."""
    world: RetentionWorld = await make_retention_world(
        pg_handoff.world.inventory, pg_handoff.world.producer, pg_handoff.world.clock
    )
    await world.seed(owner="member:alice")
    await asyncio.gather(*(world.publish() for _ in range(12)))
    commands: tuple[RetentionCommand, ...] = await world.publish()
    assert len(commands) == 1
    command: RetentionCommand = commands[0]
    receipts: tuple[RetentionReceipt, ...] = tuple(
        await asyncio.gather(*(world.receiver.receive(command) for _ in range(24)))
    )
    assert receipts.count(RetentionReceipt.APPLIED) == 1
    assert receipts.count(RetentionReceipt.DUPLICATE) == 23
    async with world.main() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(RetentionNotificationReceiptRow))
            == 1
        )
        assert (await verify_chain(session)).ok
    async with world.inventory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(InventoryRetentionOutboxRow)) == 1
        )
        with pytest.raises(ProgrammingError):
            await session.execute(
                text(f'SELECT * FROM "{pg_handoff.producer_schema}".notifications')
            )
    async with world.main() as session:
        with pytest.raises(ProgrammingError):
            await session.execute(
                text(f'SELECT * FROM "{pg_handoff.inventory_schema}".data_field_values')
            )
