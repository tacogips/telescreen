"""PostgreSQL concurrency, policy fencing and actual cross-module privilege denial."""

import asyncio
from collections import Counter
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError

from telescreen.collectors.inventory_publish import InventoryPublisher
from telescreen.core.vocabulary import InventoryMode
from telescreen.db.models import CollectorConfigRow, DataAssetRow
from telescreen.db.models.inventory_handoff import InventoryInboxRow, InventoryOutboxRow
from telescreen.ports.inventory_commands import (
    InventoryCommand,
    InventoryReceipt,
    ObserveResource,
    StopTracking,
    decode_inventory_command,
)
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.inventory.handoff.conftest import NOW, HandoffWorld
from tests.inventory.handoff.pg_fixture import PgHandoff, pg_handoff
from tests.inventory.handoff.test_delivery import FailAfterApply, observed

__all__: list[str] = ["pg_handoff"]
pytestmark: list[pytest.MarkDecorator] = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize("via_publisher", (False, True))
async def test_concurrent_producers_allocate_contiguous_positions(
    pg_handoff: PgHandoff, via_publisher: bool
) -> None:
    """First-stream creation and policy-locked publication remain atomic under contention."""
    world: HandoffWorld = pg_handoff.world
    publisher: InventoryPublisher = InventoryPublisher(
        world.outbox, SqlTaskQueue(world.clock), world.clock
    )

    async def publish(index: int) -> None:
        async with world.producer.begin() as session:
            operation: ObserveResource = observed(key=f"{index:064x}")
            if via_publisher:
                await publisher.observe(session, operation.observation)
            else:
                await world.outbox.append(session, "collector:config-id", NOW, operation)

    await asyncio.wait_for(asyncio.gather(*(publish(index) for index in range(24))), timeout=20)
    async with world.producer() as session:
        rows: tuple[InventoryOutboxRow, ...] = tuple(
            (
                await session.scalars(
                    select(InventoryOutboxRow).order_by(InventoryOutboxRow.sequence)
                )
            ).all()
        )
        assert tuple(row.sequence for row in rows) == tuple(range(1, 25))
        assert len({row.id for row in rows}) == 24


async def test_concurrent_receivers_apply_only_once(pg_handoff: PgHandoff) -> None:
    """Concurrent inbox creation and receipt replay cannot duplicate asset history."""
    world: HandoffWorld = pg_handoff.world
    command: InventoryCommand = await world.append(observed())
    receipts: list[InventoryReceipt] = await asyncio.wait_for(
        asyncio.gather(*(world.inbox.receive(command) for _ in range(12))), timeout=20
    )
    assert Counter(receipts) == Counter(
        {InventoryReceipt.APPLIED: 1, InventoryReceipt.DUPLICATE: 11}
    )
    async with world.inventory() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 1
        assert await session.scalar(select(func.count()).select_from(InventoryInboxRow)) == 1


async def test_cached_policy_waits_for_stop_tracking_commit(pg_handoff: PgHandoff) -> None:
    """A stale cached ALL policy cannot publish after OFF as a newer ALL command."""
    world: HandoffWorld = pg_handoff.world
    publisher: InventoryPublisher = InventoryPublisher(
        world.outbox, SqlTaskQueue(world.clock), world.clock
    )
    initial: InventoryCommand = await world.append(observed())
    await world.inbox.receive(initial)
    async with world.producer() as stale:
        cached: CollectorConfigRow | None = await stale.get(CollectorConfigRow, "config-id")
        assert cached is not None and cached.inventory_mode == "all"
        async with world.producer.begin() as stopper:
            config: CollectorConfigRow | None = await stopper.get(CollectorConfigRow, "config-id")
            assert config is not None
            config.inventory_mode = "off"
            await publisher.stop_tracking(stopper, "config-id", "member:admin")

            async def publish_cached() -> None:
                await publisher.observe(stale, observed(NOW + timedelta(hours=1)).observation)
                await stale.commit()

            blocked: asyncio.Task[None] = asyncio.create_task(publish_cached())
            try:
                async with world.producer() as connection:
                    for _ in range(500):
                        await connection.execute(text("SELECT pg_stat_clear_snapshot()"))
                        waiting: int = int(
                            await connection.scalar(
                                text(
                                    "SELECT count(*) FROM pg_stat_activity "
                                    "WHERE usename = :role AND wait_event_type = 'Lock'"
                                ),
                                {"role": pg_handoff.producer_schema},
                            )
                            or 0
                        )
                        if waiting:
                            break
                        await asyncio.sleep(0.01)
                    else:
                        raise AssertionError("publisher did not wait on the collector policy lock")
                assert not blocked.done()
            except BaseException:
                blocked.cancel()
                await asyncio.gather(blocked, return_exceptions=True)
                raise
        await asyncio.wait_for(blocked, timeout=10)
    async with world.producer() as session:
        rows: tuple[InventoryOutboxRow, ...] = tuple(
            (
                await session.scalars(
                    select(InventoryOutboxRow)
                    .where(InventoryOutboxRow.sequence > 1)
                    .order_by(InventoryOutboxRow.sequence)
                )
            ).all()
        )
        commands: tuple[InventoryCommand, ...] = tuple(
            decode_inventory_command(row.payload_json) for row in rows
        )
    assert isinstance(commands[0].operation, StopTracking)
    assert isinstance(commands[1].operation, ObserveResource)
    assert commands[1].operation.inventory_mode is InventoryMode.OFF
    for command in commands:
        assert await world.inbox.receive(command) is InventoryReceipt.APPLIED
    async with world.inventory() as session:
        asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
        assert asset is not None and asset.state == "unknown"


async def test_consumer_role_cannot_access_producer_tables(pg_handoff: PgHandoff) -> None:
    """Successful handoff works under credentials which forbid cross-module SQL."""
    world: HandoffWorld = pg_handoff.world
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with world.inventory() as session:
            await session.execute(
                text(f'SELECT * FROM "{pg_handoff.producer_schema}".collector_configs')
            )
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with world.producer() as session:
            await session.execute(
                text(f'SELECT * FROM "{pg_handoff.inventory_schema}".data_assets')
            )
    command: InventoryCommand = await world.append(observed())
    assert await world.dispatcher.dispatch() == 1
    assert await world.inbox.receive(command) is InventoryReceipt.DUPLICATE
    failing: FailAfterApply = FailAfterApply(world)
    retry: InventoryCommand = await world.append(observed(key="b" * 64))
    with pytest.raises(RuntimeError, match="consumer crash"):
        await replace(world.inbox, applier=failing).receive(retry)
    async with world.inventory() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 1
    assert await world.inbox.receive(retry) is InventoryReceipt.APPLIED
