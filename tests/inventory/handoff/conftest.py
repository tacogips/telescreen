"""Two physically separate databases with disjoint ownership for handoff tests."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from telescreen.core.clock import FakeClock
from telescreen.db.base import Base
from telescreen.db.models import CollectorConfigRow
from telescreen.db.session import create_sessionmaker
from telescreen.integrations.inventory_dispatch import InventoryDispatcher
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.inventory.handoff.apply import InventoryOperationApplier
from telescreen.inventory.handoff.inbox import InventoryInbox
from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.reconcile import InventoryReconcilerImpl
from telescreen.inventory.repository import InventoryRepository
from telescreen.ports.inventory_commands import InventoryCommand, InventoryOperation

NOW: datetime = datetime(2026, 10, 14, tzinfo=UTC)


@dataclass(frozen=True)
class HandoffWorld:
    """Independent producer and inventory transactions around real business services."""

    producer: async_sessionmaker[AsyncSession]
    inventory: async_sessionmaker[AsyncSession]
    clock: FakeClock
    outbox: InventoryOutbox
    applier: InventoryOperationApplier
    inbox: InventoryInbox
    dispatcher: InventoryDispatcher

    async def append(
        self,
        operation: InventoryOperation,
        at: datetime = NOW,
        stream_id: str = "collector:config-id",
    ) -> InventoryCommand:
        """Commit a producer command so it can be delivered independently."""
        async with self.producer.begin() as session:
            return await self.outbox.append(session, stream_id, at, operation)


@pytest_asyncio.fixture
async def handoff(tmp_path: Path) -> AsyncIterator[HandoffWorld]:
    """Only the tables owned by each side are installed in its database."""
    producer_engine: AsyncEngine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'producer.db'}"
    )
    inventory_engine: AsyncEngine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}"
    )
    try:
        async with producer_engine.begin() as connection:
            for name in (
                "inventory_outbox_streams",
                "inventory_outbox",
                "issues",
                "collector_configs",
                "engine_tasks",
            ):
                await connection.run_sync(Base.metadata.tables[name].create)
        async with inventory_engine.begin() as connection:
            for name in (
                "inventory_inbox_streams",
                "inventory_inbox",
                "inventory_counters",
                "data_assets",
                "data_asset_events",
                "data_asset_items",
                "data_fields",
                "data_field_values",
            ):
                await connection.run_sync(Base.metadata.tables[name].create)
        producer: async_sessionmaker[AsyncSession] = create_sessionmaker(producer_engine)
        inventory: async_sessionmaker[AsyncSession] = create_sessionmaker(inventory_engine)
        async with producer.begin() as session:
            session.add(
                CollectorConfigRow(
                    id="config-id",
                    name="input",
                    kind="custom",
                    trigger_mode="event",
                    selector_json="{}",
                    created_at=NOW,
                    updated_at=NOW,
                    inventory_mode="all",
                )
            )
        clock: FakeClock = FakeClock(NOW)
        repository: InventoryRepository = InventoryRepository()
        applier: InventoryOperationApplier = InventoryOperationApplier(
            InventoryObserverImpl(repository),
            InventoryReconcilerImpl(repository, clock),
        )
        outbox: InventoryOutbox = InventoryOutbox()
        inbox: InventoryInbox = InventoryInbox(inventory, applier, clock)
        yield HandoffWorld(
            producer,
            inventory,
            clock,
            outbox,
            applier,
            inbox,
            InventoryDispatcher(producer, outbox, inbox, clock),
        )
    finally:
        await producer_engine.dispose()
        await inventory_engine.dispose()
