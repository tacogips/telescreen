"""Route durable operations through inventory-owned business services."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.reconcile import InventoryReconcilerImpl
from telescreen.ports.inventory_commands import (
    CompleteListing,
    CompleteSweep,
    InventoryCommand,
    InventoryOperation,
    ObserveResource,
    StopTracking,
)


@dataclass(frozen=True)
class InventoryOperationApplier:
    """Apply captured commands without reading mutable producer configuration."""

    observer: InventoryObserverImpl
    reconciler: InventoryReconcilerImpl

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
        else:
            # Issue link and directory operations are applied by svc-p04-inventory.
            raise ValueError("inventory operation is not supported by this receiver")
