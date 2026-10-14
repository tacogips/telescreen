"""The main queue recovers pending inventory obligations without cross-store writes."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from telescreen.core.vocabulary import EngineTaskStatus
from telescreen.db.models import EngineTaskRow
from telescreen.integrations.retention_delivery import RetentionRecoveryProvider
from telescreen.ports.retention import RetentionCommand
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.inventory.retention.conftest import RetentionWorld


@pytest.mark.asyncio
async def test_pending_obligations_recover_without_overriding_active_backoff(
    retention_world: RetentionWorld,
) -> None:
    """A dead queue attempt never deletes the durable inventory obligation."""
    world: RetentionWorld = retention_world
    await world.seed()
    command: RetentionCommand = (await world.publish())[0]
    provider: RetentionRecoveryProvider = RetentionRecoveryProvider(
        world.outbox, SqlTaskQueue(world.clock), world.inventory
    )
    async with world.main.begin() as session:
        assert await provider.tick(session, world.clock.now()) == 1
        task: EngineTaskRow | None = await session.scalar(select(EngineTaskRow))
        assert task is not None and task.ref_id == command.event_id
        task.available_at = world.clock.now() + timedelta(days=1)
    async with world.main.begin() as session:
        assert await provider.tick(session, world.clock.now()) == 0
        task = await session.scalar(select(EngineTaskRow))
        assert task is not None
        task.status = EngineTaskStatus.DEAD.value
    world.clock.advance(timedelta(minutes=1))
    async with world.main.begin() as session:
        assert await provider.tick(session, world.clock.now()) == 1
        assert await session.scalar(select(func.count()).select_from(EngineTaskRow)) == 2
    async with world.inventory() as session:
        assert await world.outbox.get_pending(session, command.event_id) is not None


@pytest.mark.asyncio
async def test_recovery_pages_past_failed_or_active_obligations(
    retention_world: RetentionWorld,
) -> None:
    """A small poll batch still reaches later pending obligations in subsequent ticks."""
    world: RetentionWorld = retention_world
    for number in range(1, 4):
        await world.seed(f"asset-{number}", number)
    commands: tuple[RetentionCommand, ...] = await world.publish()
    provider: RetentionRecoveryProvider = RetentionRecoveryProvider(
        world.outbox, SqlTaskQueue(world.clock), world.inventory, batch_size=1
    )
    for _ in range(3):
        async with world.main.begin() as session:
            assert await provider.tick(session, world.clock.now()) == 1
    async with world.main.begin() as session:
        assert await provider.tick(session, world.clock.now()) == 0
        identifiers: tuple[str, ...] = tuple(
            (await session.scalars(select(EngineTaskRow.ref_id))).all()
        )
        assert set(identifiers) == {command.event_id for command in commands}
