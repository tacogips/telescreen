"""Producer-local durable inventory commands and sequence allocation."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models.inventory_handoff import InventoryOutboxRow, InventoryOutboxStreamRow
from telescreen.ports.inventory_commands import (
    InventoryCommand,
    InventoryOperation,
    decode_inventory_command,
    encode_inventory_command,
)


@dataclass(frozen=True)
class InventoryOutbox:
    """Append commands in the producer's transaction without accessing inventory."""

    async def append(
        self,
        session: AsyncSession,
        stream_id: str,
        occurred_at: datetime,
        operation: InventoryOperation,
    ) -> InventoryCommand:
        """Allocate a stream position and persist its immutable command together."""
        stream: InventoryOutboxStreamRow | None = await session.get(
            InventoryOutboxStreamRow,
            stream_id,
            with_for_update=True,
            populate_existing=True,
        )
        if stream is None:
            try:
                async with session.begin_nested():
                    session.add(InventoryOutboxStreamRow(id=stream_id, last_sequence=0))
                    await session.flush()
            except IntegrityError:
                pass
        sequence: int | None = await session.scalar(
            update(InventoryOutboxStreamRow)
            .where(InventoryOutboxStreamRow.id == stream_id)
            .values(last_sequence=InventoryOutboxStreamRow.last_sequence + 1)
            .returning(InventoryOutboxStreamRow.last_sequence)
        )
        if sequence is None:
            raise TelescreenError(ErrorCode.CONFLICT, "inventory stream allocation failed")
        command: InventoryCommand = InventoryCommand(
            new_id(), stream_id, sequence, occurred_at, operation
        )
        session.add(
            InventoryOutboxRow(
                id=command.event_id,
                stream_id=stream_id,
                sequence=sequence,
                payload_json=encode_inventory_command(command),
                recorded_at=occurred_at,
            )
        )
        await session.flush()
        return command

    async def pending(
        self, session: AsyncSession, limit: int, after_stream_id: str | None = None
    ) -> tuple[InventoryCommand, ...]:
        """Read one pending head per stream without holding delivery locks."""
        if limit <= 0:
            return ()
        earlier: type[InventoryOutboxRow] = aliased(InventoryOutboxRow)
        rows: tuple[InventoryOutboxRow, ...] = tuple(
            (
                await session.scalars(
                    select(InventoryOutboxRow)
                    .where(
                        InventoryOutboxRow.delivered_at.is_(None),
                        InventoryOutboxRow.stream_id > (after_stream_id or ""),
                        ~select(earlier.id)
                        .where(
                            earlier.stream_id == InventoryOutboxRow.stream_id,
                            earlier.sequence < InventoryOutboxRow.sequence,
                            earlier.delivered_at.is_(None),
                        )
                        .exists(),
                    )
                    .order_by(InventoryOutboxRow.stream_id)
                    .limit(limit)
                )
            ).all()
        )
        return tuple(decode_inventory_command(row.payload_json) for row in rows)

    async def get_pending(self, session: AsyncSession, event_id: str) -> InventoryCommand | None:
        """Load one command for its durable delivery task; acknowledged rows are no-ops."""
        row: InventoryOutboxRow | None = await session.get(InventoryOutboxRow, event_id)
        if row is None or row.delivered_at is not None:
            return None
        return decode_inventory_command(row.payload_json)

    async def acknowledge(
        self, session: AsyncSession, event_id: str, delivered_at: datetime
    ) -> None:
        """Mark a command delivered only after the inventory transaction commits."""
        await session.execute(
            update(InventoryOutboxRow)
            .where(InventoryOutboxRow.id == event_id, InventoryOutboxRow.delivered_at.is_(None))
            .values(delivered_at=delivered_at)
        )
