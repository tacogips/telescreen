"""Drain inventory audit commands into the local inventory audit chain."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.audit.chain import INVENTORY_AUDIT_MODELS
from telescreen.audit.service import AuditLogService
from telescreen.core.clock import Clock
from telescreen.inventory.audit import InventoryAuditOutbox
from telescreen.masking.redact import Redactor
from telescreen.ports.audit_commands import AuditCommand


@dataclass
class LocalAuditDrain:
    """Append and acknowledge local audit commands atomically in bounded batches."""

    sessions: async_sessionmaker[AsyncSession]
    outbox: InventoryAuditOutbox
    audit: AuditLogService
    clock: Clock
    after_id: str | None = None
    batch_size: int = 20

    async def tick(self) -> None:
        """Drain a rotating bounded batch without losing failed entries."""
        async with self.sessions() as session:
            identifiers: tuple[str, ...] = await self.outbox.pending_ids(
                session, self.batch_size, self.after_id
            )
            if not identifiers and self.after_id is not None:
                self.after_id = None
                identifiers = await self.outbox.pending_ids(session, self.batch_size)
        for event_id in identifiers:
            self.after_id = event_id
            try:
                async with self.sessions.begin() as session:
                    command: AuditCommand | None = await self.outbox.get_pending(session, event_id)
                    if command is None:
                        continue
                    await self.audit.append(session, command.entry)
                    await self.outbox.acknowledge(session, event_id, self.clock.now())
            except Exception:
                continue


def build_local_audit_drain(
    sessions: async_sessionmaker[AsyncSession], clock: Clock, redactor: Redactor
) -> LocalAuditDrain:
    """Build the inventory chain writer with store-specific models."""
    return LocalAuditDrain(
        sessions,
        InventoryAuditOutbox(),
        AuditLogService(clock, redactor, INVENTORY_AUDIT_MODELS),
        clock,
    )
