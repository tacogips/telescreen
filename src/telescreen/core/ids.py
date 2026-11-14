"""Identifier generation helpers."""

from uuid import uuid4


def new_id() -> str:
    """Return a new UUID4 identifier string."""
    return str(uuid4())
