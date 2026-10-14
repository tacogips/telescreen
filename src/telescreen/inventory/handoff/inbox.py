"""Inventory-owned command deduplication, sequencing and atomic application."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models.inventory_handoff import InventoryInboxRow, InventoryInboxStreamRow
from telescreen.ports.inventory_commands import (
    InventoryCommand,
    InventoryReceipt,
    encode_inventory_command,
)


class InventoryCommandApplier(Protocol):
    """Inventory-internal application in the same transaction as its receipt."""

    async def apply(self, session: AsyncSession, command: InventoryCommand) -> None: ...


@dataclass(frozen=True)
class InventoryInbox:
    """Apply a command once, in order, without using any producer session."""

    sessions: async_sessionmaker[AsyncSession]
    applier: InventoryCommandApplier
    clock: Clock

    async def receive(self, command: InventoryCommand) -> InventoryReceipt:
        """Commit changes and a receipt atomically; leave gaps and failures retryable."""
        digest: str = sha256(encode_inventory_command(command).encode("utf-8")).hexdigest()
        async with self.sessions.begin() as session:
            cursor: InventoryInboxStreamRow | None = await session.get(
                InventoryInboxStreamRow,
                command.stream_id,
                with_for_update=True,
                populate_existing=True,
            )
            if cursor is None:
                try:
                    async with session.begin_nested():
                        session.add(InventoryInboxStreamRow(id=command.stream_id, last_sequence=0))
                        await session.flush()
                except IntegrityError:
                    pass
                cursor = await session.get(
                    InventoryInboxStreamRow,
                    command.stream_id,
                    with_for_update=True,
                    populate_existing=True,
                )
            if cursor is None:
                raise TelescreenError(ErrorCode.CONFLICT, "inventory cursor allocation failed")
            existing: InventoryInboxRow | None = await session.scalar(
                select(InventoryInboxRow).where(InventoryInboxRow.id == command.event_id)
            )
            if existing is not None:
                if (
                    existing.stream_id != command.stream_id
                    or existing.sequence != command.sequence
                    or existing.payload_sha256 != digest
                ):
                    raise TelescreenError(ErrorCode.CONFLICT, "inventory command identity conflict")
                return InventoryReceipt.DUPLICATE
            if command.sequence <= cursor.last_sequence:
                raise TelescreenError(ErrorCode.CONFLICT, "inventory stream position conflict")
            if command.sequence != cursor.last_sequence + 1:
                return InventoryReceipt.GAP
            await self.applier.apply(session, command)
            session.add(
                InventoryInboxRow(
                    id=command.event_id,
                    stream_id=command.stream_id,
                    sequence=command.sequence,
                    payload_sha256=digest,
                    applied_at=self.clock.now(),
                )
            )
            cursor.last_sequence = command.sequence
            try:
                await session.flush()
            except IntegrityError:
                raise TelescreenError(
                    ErrorCode.CONFLICT, "inventory command identity conflict"
                ) from None
        return InventoryReceipt.APPLIED
