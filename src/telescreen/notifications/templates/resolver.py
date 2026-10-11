"""Template resolution at send time: active DB version, built-in, or event fallback."""

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import NotificationEvent
from telescreen.db.models.notifications import NotificationTemplateRow
from telescreen.notifications.templates.builtins import builtin_for, builtin_named
from telescreen.notifications.templates.model import (
    BuiltinTemplate,
    ResolvedTemplate,
    TemplateTexts,
)
from telescreen.notifications.templates.repository import TemplateRepository


class TemplateResolver(Protocol):
    """Select the template version used to render one notification request."""

    async def resolve(
        self, session: AsyncSession, name: str, event: NotificationEvent
    ) -> ResolvedTemplate:
        """Return the template for a name and event; never raises for unknown names."""
        ...


@dataclass(frozen=True)
class BuiltinTemplateResolver:
    """Resolve only the code-held built-in templates."""

    async def resolve(
        self, session: AsyncSession, name: str, event: NotificationEvent
    ) -> ResolvedTemplate:
        """Return the named built-in, else the event's built-in as a fallback."""
        del session
        return _builtin_or_fallback(name, event)


@dataclass(frozen=True)
class DbTemplateResolver:
    """Resolve the active stored version of a name before the built-ins."""

    repository: TemplateRepository = field(default_factory=TemplateRepository)

    async def resolve(
        self, session: AsyncSession, name: str, event: NotificationEvent
    ) -> ResolvedTemplate:
        """Return the active DB version, else the built-in, else the event fallback."""
        row: NotificationTemplateRow | None = await self.repository.active(session, name)
        if row is not None and row.event_kind == event.value:
            return ResolvedTemplate(row.name, row.version, event, texts_of(row))
        return _builtin_or_fallback(name, event)


def texts_of(row: NotificationTemplateRow) -> TemplateTexts:
    """Return the text fields of a stored template version."""
    return TemplateTexts(
        row.subject,
        row.body,
        row.email_subject,
        row.email_body,
        row.slack_body,
        row.description,
    )


def _builtin_or_fallback(name: str, event: NotificationEvent) -> ResolvedTemplate:
    """Return the named built-in for the event, else the event built-in marked fallback."""
    named: BuiltinTemplate | None = builtin_named(name)
    if named is not None and named.event_kind is event:
        return ResolvedTemplate(named.name, named.version, event, named.texts)
    default: BuiltinTemplate = builtin_for(event)
    return ResolvedTemplate(default.name, default.version, event, default.texts, True)
