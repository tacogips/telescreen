"""Session-free audit receiver using the audit owner's transaction."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.audit.service import AuditLogService
from telescreen.ports.audit_commands import AuditCommand, AuditReceipt


@dataclass(frozen=True)
class AuditCommandReceiver:
    """Commit chain append and deduplication receipt together before replying."""

    sessions: async_sessionmaker[AsyncSession]
    audit: AuditLogService

    async def receive(self, command: AuditCommand) -> AuditReceipt:
        """Never acknowledge an entry whose audit transaction failed to commit."""
        async with self.sessions.begin() as session:
            receipt: AuditReceipt = await self.audit.append_command(session, command)
        return receipt
