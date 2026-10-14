"""Audit log port and entry DTO."""

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.ports.audit_types import AuditEntry as AuditEntry


class AuditLog(Protocol):
    """Record audit intent atomically with a business operation.

    Owner-local logs return the chain sequence; durable publishers return zero until
    delivery. A caller must not use the returned value as the business event identity.
    """

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Append or durably stage an entry in the caller-owned transaction."""
        ...
