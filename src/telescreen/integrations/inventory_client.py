"""Typed Telescreen-to-Inventory command client."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from telescreen.inventory_service.transport import ServiceHttp
from telescreen.ports.inventory_commands import (
    InventoryCommand,
    InventoryReceipt,
    encode_inventory_command,
)


@dataclass(frozen=True)
class InventoryClient:
    """Publish inventory commands through the fixed service origin."""

    transport: ServiceHttp

    async def receive(self, command: InventoryCommand) -> InventoryReceipt:
        """Return only a validated durable receipt."""
        return await self.transport.post(
            "/commands", encode_inventory_command(command), TypeAdapter(InventoryReceipt)
        )
