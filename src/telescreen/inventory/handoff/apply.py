"""Route durable operations through inventory-owned business services."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.reconcile import InventoryReconcilerImpl
from telescreen.ports.directory_projection import DirectoryEntity
from telescreen.ports.inventory_commands import (
    CompleteListing,
    CompleteSweep,
    DirectoryUpdate,
    InventoryCommand,
    InventoryOperation,
    IssueLinkState,
    ObserveResource,
    StopTracking,
)


class IssueLinkSink(Protocol):
    """Apply an issue-link projection inside the inventory inbox transaction."""

    async def apply_link(self, session: AsyncSession, state: IssueLinkState) -> None: ...


class DirectorySink(Protocol):
    """Apply a directory projection inside the inventory inbox transaction."""

    async def apply_entity(self, session: AsyncSession, entity: DirectoryEntity) -> None: ...


@dataclass(frozen=True)
class InventoryOperationApplier:
    """Apply captured commands without reading mutable producer configuration."""

    observer: InventoryObserverImpl
    reconciler: InventoryReconcilerImpl
    links: IssueLinkSink | None = None
    directory: DirectorySink | None = None

    async def apply(self, session: AsyncSession, command: InventoryCommand) -> None:
        """Use the receiving transaction for inventory changes and lifecycle history."""
        operation: InventoryOperation = command.operation
        if isinstance(operation, ObserveResource):
            await self.observer.apply_observation(
                session, operation.observation, operation.inventory_mode
            )
        elif isinstance(operation, CompleteSweep):
            await self.reconciler.reconcile_sweep(
                session,
                operation.collector_config_id,
                operation.seen_before,
                mode=operation.inventory_mode,
                recorded_at=command.occurred_at,
            )
        elif isinstance(operation, CompleteListing):
            await self.reconciler.reconcile_listing(
                session,
                operation.collector_config_id,
                frozenset(operation.listed_keys),
                mode=operation.inventory_mode,
                recorded_at=command.occurred_at,
            )
        elif isinstance(operation, StopTracking):
            await self.reconciler.stop_tracking(
                session,
                operation.collector_config_id,
                operation.actor_ref,
                recorded_at=command.occurred_at,
            )
        elif isinstance(operation, IssueLinkState):
            if self.links is None:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "unsupported inventory operation")
            await self.links.apply_link(session, operation)
        elif isinstance(operation, DirectoryUpdate):
            if self.directory is None:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "unsupported inventory operation")
            await self.directory.apply_entity(session, operation.entity)
        else:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "unsupported inventory operation")
