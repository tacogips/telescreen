"""Session-free identity contracts used by inventory consumers."""

from dataclasses import dataclass
from typing import Protocol

from telescreen.core.vocabulary import ActorDisplayKind, ActorKind


@dataclass(frozen=True)
class AssigneeIds:
    """Resolved member or team assignment identifiers."""

    member_id: str | None
    team_id: str | None


@dataclass(frozen=True)
class ActorDisplay:
    """Safe display identity resolved from an actor reference."""

    ref: str
    kind: ActorDisplayKind
    display_name: str
    handle: str | None
    active: bool


class ActorLookup(Protocol):
    """Resolve display identities without receiving the caller's transaction."""

    async def display(self, refs: tuple[str, ...]) -> tuple[ActorDisplay, ...]:
        """Preserve the length and order of the input references."""
        ...


class AssigneeLookup(Protocol):
    """Check member and team references in the identity owner's database."""

    async def assignee_of(self, ref: str) -> AssigneeIds:
        """Resolve an assignment or raise recipient_unknown."""
        ...


def actor_kind_for(actor_ref: str) -> ActorKind:
    """Classify an actor reference by its prefix for audit entries."""
    if actor_ref.startswith("member:"):
        return ActorKind.MEMBER
    if actor_ref.startswith("token:"):
        return ActorKind.TOKEN
    return ActorKind.SYSTEM
