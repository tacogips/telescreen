"""Notification template catalog port and public metadata DTO."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import NotificationEvent


@dataclass(frozen=True)
class TemplateInfo:
    """Metadata required to resolve a named notification template."""

    name: str
    event_kind: NotificationEvent
    active_version: int | None


class TemplateCatalog(Protocol):
    """Look up active notification template metadata in a caller transaction."""

    async def lookup(self, session: AsyncSession, name: str) -> TemplateInfo | None:
        """Return template metadata, or None when the name is unknown."""
        ...
