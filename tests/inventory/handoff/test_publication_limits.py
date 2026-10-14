"""Oversized publication rolls back the producing business transaction and stream allocation."""

import pytest
from sqlalchemy import func, select

from telescreen.collectors.inventory_publish import InventoryPublisher
from telescreen.core.errors import TelescreenError
from telescreen.db.models import CollectorConfigRow, EngineTaskRow, InventoryOutboxRow
from telescreen.db.models.inventory_handoff import InventoryOutboxStreamRow
from telescreen.ports.handoff_limits import MAX_INVENTORY_ENTRIES
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.inventory.handoff.conftest import HandoffWorld


@pytest.mark.asyncio
async def test_oversized_listing_rolls_back_business_outbox_queue_and_sequence(
    handoff: HandoffWorld,
) -> None:
    """No incomplete listing or partial producer commit survives a size-limit failure."""
    publisher: InventoryPublisher = InventoryPublisher(
        handoff.outbox, SqlTaskQueue(handoff.clock), handoff.clock
    )
    with pytest.raises(TelescreenError, match="snapshot exceeds supported limit"):
        async with handoff.producer.begin() as session:
            config: CollectorConfigRow | None = await session.get(CollectorConfigRow, "config-id")
            assert config is not None
            config.enabled = False
            await session.flush()
            await publisher.reconcile_listing(
                session,
                "config-id",
                frozenset(str(index) for index in range(MAX_INVENTORY_ENTRIES + 1)),
            )
    async with handoff.producer() as session:
        config = await session.get(CollectorConfigRow, "config-id")
        assert config is not None and config.enabled
        for model in (InventoryOutboxRow, InventoryOutboxStreamRow, EngineTaskRow):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
