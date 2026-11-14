"""Producer publication, delivery tasks and durable recovery behavior."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from telescreen.collectors.inventory_publish import InventoryPublisher
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind, EngineTaskStatus, ErrorCode, InventoryMode
from telescreen.db.models import CollectorConfigRow, DataAssetRow, EngineTaskRow
from telescreen.integrations.inventory_delivery import (
    InventoryDeliveryHandler,
    InventoryRecoveryProvider,
)
from telescreen.ports.inventory_commands import InventoryCommand, ObserveResource
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.inventory.handoff.conftest import NOW, HandoffWorld
from tests.inventory.handoff.test_delivery import CrashAfterCommit, observed


def task(command: InventoryCommand) -> ClaimedTask:
    """Describe the already-persisted command's worker invocation."""
    return ClaimedTask(
        "delivery",
        EngineTaskKind.INVENTORY_DELIVERY,
        command.event_id,
        json_null(),
        1,
        5,
        f"inventory-command:{command.event_id}",
    )


@pytest.mark.asyncio
async def test_publisher_and_delivery_use_only_their_owned_databases(handoff: HandoffWorld) -> None:
    """Publication does not query inventory; task delivery reads no producer config there."""
    publisher: InventoryPublisher = InventoryPublisher(
        handoff.outbox, SqlTaskQueue(handoff.clock), handoff.clock
    )
    async with handoff.producer.begin() as session:
        await publisher.observe(session, observed().observation)
    async with handoff.producer() as session:
        commands: tuple[InventoryCommand, ...] = await handoff.outbox.pending(session, 10)
        queued: EngineTaskRow | None = await session.scalar(select(EngineTaskRow))
        assert queued is not None and queued.ref_id == commands[0].event_id
        assert isinstance(commands[0].operation, ObserveResource)
        assert commands[0].operation.inventory_mode is InventoryMode.ALL
    async with handoff.inventory() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 0
    handler: InventoryDeliveryHandler = InventoryDeliveryHandler(
        handoff.producer, handoff.outbox, handoff.inbox, handoff.clock
    )
    assert (await handler.handle(task(commands[0]))).kind is TaskResultKind.DONE
    async with handoff.inventory() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 1


@pytest.mark.asyncio
async def test_policy_change_command_and_wakeup_rollback_together(handoff: HandoffWorld) -> None:
    """A failed configuration transaction cannot stop inventory or leave a task behind."""
    publisher: InventoryPublisher = InventoryPublisher(
        handoff.outbox, SqlTaskQueue(handoff.clock), handoff.clock
    )
    with pytest.raises(RuntimeError, match="rollback"):
        async with handoff.producer.begin() as session:
            config: CollectorConfigRow | None = await session.get(CollectorConfigRow, "config-id")
            assert config is not None
            config.inventory_mode = InventoryMode.OFF.value
            await publisher.stop_tracking(session, "config-id", "member:admin")
            raise RuntimeError("rollback")
    async with handoff.producer() as session:
        config = await session.get(CollectorConfigRow, "config-id")
        assert config is not None and config.inventory_mode == InventoryMode.ALL.value
        assert await handoff.outbox.pending(session, 10) == ()
        assert await session.scalar(select(func.count()).select_from(EngineTaskRow)) == 0


@pytest.mark.asyncio
async def test_gap_retries_without_acknowledgment(handoff: HandoffWorld) -> None:
    """A task arriving before its predecessor retains its outbox row and retry budget."""
    first: InventoryCommand = await handoff.append(observed())
    second: InventoryCommand = await handoff.append(observed())
    handler: InventoryDeliveryHandler = InventoryDeliveryHandler(
        handoff.producer, handoff.outbox, handoff.inbox, handoff.clock
    )
    result: TaskResult = await handler.handle(task(second))
    assert result.kind is TaskResultKind.RETRY and result.error_code is ErrorCode.CONFLICT
    async with handoff.producer() as session:
        assert await handoff.outbox.get_pending(session, second.event_id) == second
    await handler.handle(task(first))
    assert (await handler.handle(task(second))).kind is TaskResultKind.DONE


@pytest.mark.asyncio
async def test_worker_redelivery_after_lost_receipt_is_idempotent(handoff: HandoffWorld) -> None:
    """A worker retry after a committed-but-lost response acknowledges the original event."""
    command: InventoryCommand = await handoff.append(observed())
    handler: InventoryDeliveryHandler = InventoryDeliveryHandler(
        handoff.producer, handoff.outbox, CrashAfterCommit(handoff.inbox), handoff.clock
    )
    with pytest.raises(RuntimeError, match="lost receipt"):
        await handler.handle(task(command))
    assert (await handler.handle(task(command))).kind is TaskResultKind.DONE
    async with handoff.inventory() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 1


@pytest.mark.asyncio
async def test_recovery_requeues_exhausted_work_and_preserves_active_backoff(
    handoff: HandoffWorld,
) -> None:
    """A terminal engine task never makes its inventory command unrecoverable."""
    queue: SqlTaskQueue = SqlTaskQueue(handoff.clock)
    publisher: InventoryPublisher = InventoryPublisher(handoff.outbox, queue, handoff.clock)
    recovery: InventoryRecoveryProvider = InventoryRecoveryProvider(handoff.outbox, queue)
    async with handoff.producer.begin() as session:
        await publisher.observe(session, observed().observation)
    async with handoff.producer.begin() as session:
        assert await recovery.tick(session, NOW) == 0
        primary: EngineTaskRow | None = await session.scalar(select(EngineTaskRow))
        assert primary is not None
        primary.status = EngineTaskStatus.DEAD.value
        primary.attempts = primary.max_attempts
    async with handoff.producer.begin() as session:
        assert await recovery.tick(session, NOW) == 1
    async with handoff.producer.begin() as session:
        assert await recovery.tick(session, NOW + timedelta(minutes=1)) == 0
        tasks: tuple[EngineTaskRow, ...] = tuple(
            (await session.scalars(select(EngineTaskRow))).all()
        )
        assert len(tasks) == 2
        assert sum(row.status == EngineTaskStatus.READY.value for row in tasks) == 1


@pytest.mark.asyncio
async def test_recovery_walks_past_an_unavailable_stream(handoff: HandoffWorld) -> None:
    """Recovery uses a rotating stream cursor rather than repeatedly selecting the oldest head."""
    for stream in ("collector:a", "collector:b", "collector:c"):
        await handoff.append(observed(), stream_id=stream)
    recovery: InventoryRecoveryProvider = InventoryRecoveryProvider(
        handoff.outbox, SqlTaskQueue(handoff.clock), batch_size=1
    )
    for _ in range(3):
        async with handoff.producer.begin() as session:
            assert await recovery.tick(session, NOW) == 1
    async with handoff.producer.begin() as session:
        assert await recovery.tick(session, NOW) == 0
        assert await session.scalar(select(func.count()).select_from(EngineTaskRow)) == 3
