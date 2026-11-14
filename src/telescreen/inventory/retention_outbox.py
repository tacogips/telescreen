"""Inventory-owned retention publication and detached eligibility reads."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.json import parse_json
from telescreen.core.vocabulary import DataAssetState, DataFieldRole
from telescreen.db.models import DataAssetRow, DataFieldRow, DataFieldValueRow
from telescreen.db.models.retention_handoff import InventoryRetentionOutboxRow
from telescreen.ports.retention import (
    RetentionCommand,
    RetentionStatus,
    decode_retention_command,
)


@dataclass(frozen=True)
class RetentionOutbox:
    """Read value-only commands and acknowledge committed notification receipts."""

    async def pending_ids(
        self, session: AsyncSession, limit: int, after_id: str | None = None
    ) -> tuple[str, ...]:
        """Page pending IDs fairly without allowing one failed event to hide later ones."""
        return tuple(
            (
                await session.scalars(
                    select(InventoryRetentionOutboxRow.id)
                    .where(
                        InventoryRetentionOutboxRow.delivered_at.is_(None),
                        InventoryRetentionOutboxRow.id > (after_id or ""),
                    )
                    .order_by(InventoryRetentionOutboxRow.id)
                    .limit(limit)
                )
            ).all()
        )

    async def get_pending(self, session: AsyncSession, event_id: str) -> RetentionCommand | None:
        """Close the inventory read session before calling the notification owner."""
        row: InventoryRetentionOutboxRow | None = await session.get(
            InventoryRetentionOutboxRow, event_id
        )
        return (
            decode_retention_command(row.payload_json) if row and row.delivered_at is None else None
        )

    async def acknowledge(self, session: AsyncSession, event_id: str, at: datetime) -> None:
        """Mark completed handoff; notification delivery state stays with notifications."""
        await session.execute(
            update(InventoryRetentionOutboxRow)
            .where(
                InventoryRetentionOutboxRow.id == event_id,
                InventoryRetentionOutboxRow.delivered_at.is_(None),
            )
            .values(delivered_at=ensure_utc(at))
        )


@dataclass(frozen=True)
class InventoryRetentionLookup:
    """Revalidate a due-date revision and read the inventory master's current owner."""

    sessions: async_sessionmaker[AsyncSession]
    clock: Clock

    async def status(self, command: RetentionCommand) -> RetentionStatus:
        """A superseded, archived, cleared or deleted obligation cannot generate notices."""
        async with self.sessions() as session:
            eligible: str | None = await session.scalar(
                select(DataAssetRow.id)
                .join(DataFieldValueRow, DataFieldValueRow.asset_id == DataAssetRow.id)
                .join(DataFieldRow, DataFieldRow.id == DataFieldValueRow.field_id)
                .where(
                    DataAssetRow.id == command.asset_id,
                    DataAssetRow.state != DataAssetState.DELETED.value,
                    DataAssetRow.source == command.source,
                    DataAssetRow.resource_key == command.resource_key,
                    DataFieldRow.id == command.due_field_id,
                    DataFieldRow.role == DataFieldRole.DELETION_DUE.value,
                    DataFieldRow.archived_at.is_(None),
                    DataFieldValueRow.revision_id == command.due_revision_id,
                    DataFieldValueRow.value_date == command.context.data_deletion_due,
                    DataFieldValueRow.value_date < self.clock.now().date().isoformat(),
                )
            )
            if eligible is None:
                return RetentionStatus(False)
            owner_field: DataFieldRow | None = await session.scalar(
                select(DataFieldRow).where(
                    DataFieldRow.role == DataFieldRole.OWNER.value,
                    DataFieldRow.archived_at.is_(None),
                )
            )
            owner: DataFieldValueRow | None = (
                await session.get(DataFieldValueRow, (command.asset_id, owner_field.id))
                if owner_field is not None
                else None
            )
            return RetentionStatus(True, parse_json(owner.value_json).as_str() if owner else None)
