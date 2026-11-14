"""Inventory-local audit publication; no audit tables or shared chain writes."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.ids import new_id
from telescreen.db.models.audit_handoff import InventoryAuditOutboxRow
from telescreen.masking.audit import redact_audit_entry
from telescreen.masking.redact import Redactor
from telescreen.ports.audit_commands import AuditCommand, decode_audit_command, encode_audit_command
from telescreen.ports.audit_types import AuditEntry


@dataclass(frozen=True)
class InventoryAuditPublisher:
    """Stage a masked audit entry in the same transaction as the asset change."""

    clock: Clock
    redactor: Redactor

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Return zero for a staged entry; the audit owner later allocates its sequence."""
        command: AuditCommand = AuditCommand(
            new_id(), ensure_utc(self.clock.now()), redact_audit_entry(entry, self.redactor)
        )
        session.add(
            InventoryAuditOutboxRow(
                id=command.event_id,
                payload_json=encode_audit_command(command),
                recorded_at=command.occurred_at,
            )
        )
        await session.flush()
        return 0


@dataclass(frozen=True)
class InventoryAuditOutbox:
    """Read detached commands and acknowledge only committed audit receipts."""

    async def pending_ids(
        self, session: AsyncSession, limit: int, after_id: str | None = None
    ) -> tuple[str, ...]:
        """Page pending event identities without hiding later entries behind a failure."""
        return tuple(
            (
                await session.scalars(
                    select(InventoryAuditOutboxRow.id)
                    .where(
                        InventoryAuditOutboxRow.delivered_at.is_(None),
                        InventoryAuditOutboxRow.id > (after_id or ""),
                    )
                    .order_by(InventoryAuditOutboxRow.id)
                    .limit(limit)
                )
            ).all()
        )

    async def get_pending(self, session: AsyncSession, event_id: str) -> AuditCommand | None:
        """Return value data without allowing ORM rows to cross the boundary."""
        row: InventoryAuditOutboxRow | None = await session.get(InventoryAuditOutboxRow, event_id)
        return decode_audit_command(row.payload_json) if row and row.delivered_at is None else None

    async def acknowledge(self, session: AsyncSession, event_id: str, at: datetime) -> None:
        """Record the audit owner's durable receipt in a new inventory transaction."""
        await session.execute(
            update(InventoryAuditOutboxRow)
            .where(
                InventoryAuditOutboxRow.id == event_id,
                InventoryAuditOutboxRow.delivered_at.is_(None),
            )
            .values(delivered_at=ensure_utc(at))
        )
