"""Audit log port and entry DTO."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import ActorKind


@dataclass(frozen=True)
class AuditEntry:
    """A masked audit event to append inside the caller's transaction."""

    actor_kind: ActorKind
    actor_ref: str
    action: str
    target_kind: str
    target_id: str
    summary: str
    details: JsonValue


class AuditLog(Protocol):
    """Append audit events in an existing transaction."""

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Append one event and return its sequence number."""
        ...
