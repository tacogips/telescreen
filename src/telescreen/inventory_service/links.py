"""Inventory-local stores for pushed issue and directory projections."""

from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.db.models.inventory_links import InventoryIssueLinkRow
from telescreen.inventory.handoff.apply import DirectorySink, IssueLinkSink
from telescreen.ports.directory_projection import DirectoryEntity
from telescreen.ports.inventory_commands import IssueLinkState
from telescreen.replica.store import DirectoryReplica


@dataclass(frozen=True)
class IssueLinkStore(IssueLinkSink):
    """Apply issue-link changes in the inventory inbox transaction."""

    async def apply_link(self, session: AsyncSession, state: IssueLinkState) -> None:
        """Upsert a present link or remove its issue row."""
        if not state.present:
            await session.execute(
                delete(InventoryIssueLinkRow).where(
                    InventoryIssueLinkRow.issue_id == state.issue_id
                )
            )
            return
        row: InventoryIssueLinkRow | None = await session.get(InventoryIssueLinkRow, state.issue_id)
        if row is None:
            session.add(
                InventoryIssueLinkRow(
                    issue_id=state.issue_id,
                    resource_key=state.resource_key,
                    short_id=state.short_id,
                    status=state.status,
                    owner_ref=state.owner_ref,
                )
            )
        else:
            row.resource_key = state.resource_key
            row.short_id = state.short_id
            row.status = state.status
            row.owner_ref = state.owner_ref
        await session.flush()


@dataclass(frozen=True)
class ReplicaDirectorySink(DirectorySink):
    """Apply pushed directory entities with only-if-newer revision semantics."""

    replica: DirectoryReplica

    async def apply_entity(self, session: AsyncSession, entity: DirectoryEntity) -> None:
        """Keep directory updates in the ordered inbox transaction."""
        await self.replica.apply(session, entity)
