"""Identity-owned adapters for session-free inventory enrichment."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.ports.directory import ActorDirectory, RecipientResolver
from telescreen.ports.identity_context import ActorDisplay, AssigneeIds


@dataclass(frozen=True)
class IdentityActorLookup:
    """Resolve actor displays in an independent identity read session."""

    sessions: async_sessionmaker[AsyncSession]
    directory: ActorDirectory

    async def display(self, refs: tuple[str, ...]) -> tuple[ActorDisplay, ...]:
        """Return detached identities after closing the owning read session."""
        if not refs:
            return ()
        async with self.sessions() as session:
            return await self.directory.display(session, refs)


@dataclass(frozen=True)
class IdentityAssigneeLookup:
    """Validate references using identity tables rather than inventory tables."""

    sessions: async_sessionmaker[AsyncSession]
    recipients: RecipientResolver

    async def assignee_of(self, ref: str) -> AssigneeIds:
        """Return detached assignment identifiers from an identity read session."""
        async with self.sessions() as session:
            return await self.recipients.assignee_of(session, ref)
