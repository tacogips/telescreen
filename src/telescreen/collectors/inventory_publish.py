"""Producer-local inventory publication with serialized collector policy capture."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind, InventoryMode
from telescreen.db.models.collectors import CollectorConfigRow
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.ports.inventory_commands import (
    CompleteListing,
    CompleteSweep,
    InventoryCommand,
    InventoryOperation,
    ObserveResource,
    StopTracking,
)
from telescreen.ports.inventory_types import ResourceObservation
from telescreen.ports.workflow import TaskQueue, TaskSpec


@dataclass(frozen=True)
class InventoryPublisher:
    """Write only producer-owned outbox and queue rows in the caller's transaction."""

    outbox: InventoryOutbox
    queue: TaskQueue
    clock: Clock

    async def observe(self, session: AsyncSession, observation: ResourceObservation) -> None:
        """Capture inventory policy under the collector lock before allocating a sequence."""
        mode: InventoryMode = InventoryMode.SENSITIVE_ONLY
        if observation.collector_config_id is not None:
            captured: InventoryMode | None = await self._policy(
                session, observation.collector_config_id
            )
            if captured is not None:
                mode = captured
        stream: str = (
            f"collector:{observation.collector_config_id}"
            if observation.collector_config_id is not None
            else f"source:{observation.source}"
        )
        await self._emit(session, stream, ObserveResource(observation, mode))

    async def reconcile_sweep(
        self, session: AsyncSession, collector_config_id: str, seen_before: datetime
    ) -> int:
        """Publish the sweep barrier; no inventory rows change in this transaction."""
        mode: InventoryMode = await self._policy(session, collector_config_id) or InventoryMode.OFF
        await self._emit(
            session,
            f"collector:{collector_config_id}",
            CompleteSweep(collector_config_id, seen_before, mode),
        )
        return 0

    async def reconcile_listing(
        self, session: AsyncSession, collector_config_id: str, listed_keys: frozenset[str]
    ) -> int:
        """Publish a complete listing in the same stream as its observations."""
        mode: InventoryMode = await self._policy(session, collector_config_id) or InventoryMode.OFF
        await self._emit(
            session,
            f"collector:{collector_config_id}",
            CompleteListing(collector_config_id, tuple(sorted(listed_keys)), mode),
        )
        return 0

    async def stop_tracking(
        self, session: AsyncSession, collector_config_id: str, actor_ref: str
    ) -> int:
        """Publish the tracking change atomically with the caller's configuration update."""
        await self._policy(session, collector_config_id)
        await self._emit(
            session,
            f"collector:{collector_config_id}",
            StopTracking(collector_config_id, actor_ref),
        )
        return 0

    async def _policy(
        self, session: AsyncSession, collector_config_id: str
    ) -> InventoryMode | None:
        """Always acquire collector then stream locks, refreshing any cached policy."""
        config: CollectorConfigRow | None = await session.get(
            CollectorConfigRow,
            collector_config_id,
            with_for_update=True,
            populate_existing=True,
        )
        return InventoryMode(config.inventory_mode) if config is not None else None

    async def _emit(
        self, session: AsyncSession, stream: str, operation: InventoryOperation
    ) -> None:
        """Commit a durable wakeup and its immutable outbox command together."""
        now: datetime = self.clock.now()
        command: InventoryCommand = await self.outbox.append(session, stream, now, operation)
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.INVENTORY_DELIVERY,
                command.event_id,
                json_null(),
                now,
                f"inventory-command:{command.event_id}",
            ),
        )
