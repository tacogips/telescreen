"""At-least-once delivery between independently owned databases."""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.ports.inventory_commands import (
    InventoryCommand,
    InventoryCommandReceiver,
    InventoryReceipt,
)

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InventoryDispatcher:
    """Deliver detached commands without holding producer locks during receipt."""

    sessions: async_sessionmaker[AsyncSession]
    outbox: InventoryOutbox
    receiver: InventoryCommandReceiver
    clock: Clock

    async def dispatch(self, limit: int = 100) -> int:
        """Acknowledge only committed applications; failed or gapped rows stay pending."""
        async with self.sessions() as session:
            commands: tuple[InventoryCommand, ...] = await self.outbox.pending(session, limit)
        delivered: int = 0
        blocked: set[str] = set()
        for command in commands:
            if command.stream_id in blocked:
                continue
            try:
                receipt: InventoryReceipt = await self.receiver.receive(command)
                if receipt is InventoryReceipt.GAP:
                    blocked.add(command.stream_id)
                    continue
                async with self.sessions.begin() as session:
                    await self.outbox.acknowledge(session, command.event_id, self.clock.now())
                delivered += 1
            except Exception as error:
                code: str = error.code.value if isinstance(error, TelescreenError) else "unexpected"
                logger.warning("inventory command delivery failed code=%s", code)
                # Never acknowledge a failed or uncertain delivery. A later dispatch retries
                # it with the same ID, including a crash after inventory committed.
                blocked.add(command.stream_id)
        return delivered
