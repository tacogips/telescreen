"""Dependencies shared by Reporting Bureau API routers."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.ports.audit import AuditLog
from telescreen.ports.identity import BearerVerifier, MemberProvisioner


@dataclass(frozen=True)
class ApiContext:
    """Shared request-time services for API routes."""

    sessions: async_sessionmaker[AsyncSession]
    clock: Clock
    audit: AuditLog
    verifier: BearerVerifier | None = None
    identities: MemberProvisioner | None = None
