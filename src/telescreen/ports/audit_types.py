"""Persistence-independent masked audit event data."""

from dataclasses import dataclass

from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import ActorKind


@dataclass(frozen=True)
class AuditEntry:
    """A masked audit event to append with its owning business operation."""

    actor_kind: ActorKind
    actor_ref: str
    action: str
    target_kind: str
    target_id: str
    summary: str
    details: JsonValue
