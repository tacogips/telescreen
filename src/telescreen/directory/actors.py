"""Audit actor classification for directory operations."""

from telescreen.core.vocabulary import ActorKind


def actor_kind_for(actor_ref: str) -> ActorKind:
    """Classify an actor reference by its prefix for audit entries."""
    if actor_ref.startswith("member:"):
        return ActorKind.MEMBER
    if actor_ref.startswith("token:"):
        return ActorKind.TOKEN
    return ActorKind.SYSTEM
