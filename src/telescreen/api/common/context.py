"""Dependencies shared by Reporting Bureau API routers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.ports.audit import AuditLog
from telescreen.ports.identity import BearerVerifier, MemberProvisioner
from telescreen.ports.principal import Principal


@dataclass(frozen=True)
class ApiContext:
    """Shared request-time services for API routes."""

    sessions: async_sessionmaker[AsyncSession]
    clock: Clock
    audit: AuditLog
    verifier: BearerVerifier | None = None
    identities: MemberProvisioner | None = None
    authentication: Callable[[Request], Awaitable[Principal]] | None = None
