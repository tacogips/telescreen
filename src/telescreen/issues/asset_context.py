"""In-process issue gateways with issue-owned read transactions."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.ports.inventory import AssetOwnerResolver
from telescreen.ports.issues import ResourceIssueReader, ResourceIssueRef


@dataclass(frozen=True)
class IssueAssetLookup:
    """Expose issue references as detached values using the issue database."""

    sessions: async_sessionmaker[AsyncSession]
    reader: ResourceIssueReader

    async def open_issues_for_resources(
        self, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Read committed issue state in an independent transaction."""
        if not resource_keys:
            return ()
        async with self.sessions() as session:
            return await self.reader.open_issues_for_resources(session, resource_keys)


@dataclass(frozen=True)
class IssueAssetOwnerLookup:
    """Keep fallback owner SQL and its transaction inside issue management."""

    sessions: async_sessionmaker[AsyncSession]
    resolver: AssetOwnerResolver

    async def fallback_owner(
        self, source: str, resource_key: str, display_locator: str
    ) -> str | None:
        """Resolve committed issue ownership without an inventory session."""
        async with self.sessions() as session:
            return await self.resolver.fallback_owner(
                session, source, resource_key, display_locator
            )
