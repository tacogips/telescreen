"""Session-owning inventory enrichment adapters backed by local projections."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.vocabulary import OPEN_STATUSES, RECONCILED_STATUSES
from telescreen.db.models.inventory_links import InventoryIssueLinkRow
from telescreen.ports.asset_context import AssetIssueLookup, AssetOwnerLookup, ResourceIssueRef
from telescreen.ports.directory import AssigneeIds
from telescreen.ports.identity_context import ActorDisplay, ActorLookup, AssigneeLookup
from telescreen.replica.directory import (
    ReplicaActorDirectory,
    ReplicaOwnerDirectory,
    ReplicaRecipientResolver,
)
from telescreen.replica.rules import ReplicaOwnershipRules


@dataclass(frozen=True)
class ReplicaIssueLookup(AssetIssueLookup):
    """Read open issue links from the inventory projection."""

    sessions: async_sessionmaker[AsyncSession]

    async def open_issues_for_resources(
        self, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Return matching issue references in resource and issue order."""
        if not resource_keys:
            return ()
        async with self.sessions() as session:
            rows: tuple[InventoryIssueLinkRow, ...] = tuple(
                (
                    await session.scalars(
                        select(InventoryIssueLinkRow)
                        .where(
                            InventoryIssueLinkRow.resource_key.in_(resource_keys),
                            InventoryIssueLinkRow.status.in_(
                                tuple(status.value for status in OPEN_STATUSES)
                            ),
                        )
                        .order_by(
                            InventoryIssueLinkRow.resource_key, InventoryIssueLinkRow.issue_id
                        )
                    )
                ).all()
            )
        return tuple(ResourceIssueRef(row.resource_key, row.issue_id, row.short_id) for row in rows)


@dataclass(frozen=True)
class ReplicaActorLookup(ActorLookup):
    """Resolve safe actor displays from the inventory directory replica."""

    sessions: async_sessionmaker[AsyncSession]
    directory: ReplicaActorDirectory

    async def display(self, refs: tuple[str, ...]) -> tuple[ActorDisplay, ...]:
        """Preserve input order while owning the replica read session."""
        async with self.sessions() as session:
            return await self.directory.display(session, refs)


@dataclass(frozen=True)
class ReplicaAssigneeLookup(AssigneeLookup):
    """Resolve assignee references from the inventory directory replica."""

    sessions: async_sessionmaker[AsyncSession]
    recipients: ReplicaRecipientResolver

    async def assignee_of(self, ref: str) -> AssigneeIds:
        """Own the read session used to validate a member or team reference."""
        async with self.sessions() as session:
            return await self.recipients.assignee_of(session, ref)


@dataclass(frozen=True)
class ReplicaAssetOwnerLookup(AssetOwnerLookup):
    """Resolve fallback ownership from issue links then replica ownership rules."""

    sessions: async_sessionmaker[AsyncSession]
    owners: ReplicaOwnerDirectory
    rules: ReplicaOwnershipRules

    async def fallback_owner(
        self, source: str, resource_key: str, display_locator: str
    ) -> str | None:
        """Return the first active issue owner or matching rules team."""
        async with self.sessions() as session:
            return await self.fallback_owner_in_session(
                session, source, resource_key, display_locator
            )

    async def fallback_owner_in_session(
        self, session: AsyncSession, source: str, resource_key: str, display_locator: str
    ) -> str | None:
        """Resolve ownership using the current dispatch transaction."""
        row: InventoryIssueLinkRow | None = await session.scalar(
            select(InventoryIssueLinkRow)
            .where(
                InventoryIssueLinkRow.resource_key == resource_key,
                InventoryIssueLinkRow.status.in_(
                    tuple(status.value for status in RECONCILED_STATUSES)
                ),
                InventoryIssueLinkRow.owner_ref.is_not(None),
            )
            .order_by(InventoryIssueLinkRow.issue_id)
        )
        if row is not None and row.owner_ref is not None:
            member: str | None = await self.owners.member_for_owner(session, row.owner_ref)
            if member is not None:
                return member
        return await self.rules.match(session, source, display_locator)
