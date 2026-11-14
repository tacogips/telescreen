"""Template catalog service: list, get, validate, preview, create version, activate."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, json_array, json_int, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    ErrorCode,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.db.models.notifications import NotificationTemplateRow
from telescreen.masking.redact import Redactor
from telescreen.notifications.dlp import CensoredMessage, DlpGate, OutboundMessage
from telescreen.notifications.templates.builtins import BUILTIN_TEMPLATES, builtin_named
from telescreen.notifications.templates.model import (
    BuiltinTemplate,
    PreviewResult,
    PreviewVariant,
    TemplateIssue,
    TemplateTexts,
    TemplateValidationError,
)
from telescreen.notifications.templates.render import render_for_channel
from telescreen.notifications.templates.repository import TemplateRepository
from telescreen.notifications.templates.resolver import texts_of
from telescreen.notifications.templates.synthetic import synthetic_context
from telescreen.notifications.templates.validate import validate_template
from telescreen.notifications.templates.variables import allowed_paths
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.templates import TemplateInfo


@dataclass(frozen=True)
class TemplateSummary:
    """One template name in the merged built-in and stored catalog."""

    name: str
    event_kind: NotificationEvent
    builtin: bool
    description: str | None
    active_version: int | None
    latest_version: int
    updated_at: datetime | None


@dataclass(frozen=True)
class TemplateVersionInfo:
    """Metadata of one version of a template name (version 0 is the built-in)."""

    version: int
    active: bool
    created_by: str
    created_at: datetime | None


@dataclass(frozen=True)
class TemplateDetail:
    """A template name with its versions and the variables allowed for its event."""

    name: str
    event_kind: NotificationEvent
    builtin: bool
    active_version: int | None
    versions: tuple[TemplateVersionInfo, ...]
    variables: tuple[str, ...]


@dataclass(frozen=True)
class TemplateVersionView:
    """The text fields of one template version."""

    name: str
    version: int
    event_kind: NotificationEvent
    active: bool
    texts: TemplateTexts = field(repr=False)


@dataclass(frozen=True)
class TemplateService:
    """Manage template versions in caller transactions; also a `TemplateCatalog`."""

    repository: TemplateRepository
    redactor: Redactor
    gate: DlpGate
    audit: AuditLog
    clock: Clock

    async def list_templates(self, session: AsyncSession) -> tuple[TemplateSummary, ...]:
        """Return built-in names merged with stored names, sorted by name."""
        names: list[str] = [template.name for template in BUILTIN_TEMPLATES]
        names.extend(name for name in await self.repository.names(session) if name not in names)
        summaries: list[TemplateSummary] = []
        for name in sorted(names):
            rows: tuple[NotificationTemplateRow, ...] = await self.repository.versions(
                session, name
            )
            builtin: BuiltinTemplate | None = builtin_named(name)
            latest: NotificationTemplateRow | None = rows[-1] if rows else None
            summaries.append(
                TemplateSummary(
                    name,
                    _event_of(rows, builtin),
                    builtin is not None,
                    latest.description if latest is not None else None,
                    _active_version(rows, builtin),
                    latest.version if latest is not None else 0,
                    latest.created_at if latest is not None else None,
                )
            )
        return tuple(summaries)

    async def get(self, session: AsyncSession, name: str) -> TemplateDetail:
        """Return a template name's versions, or raise not_found."""
        rows: tuple[NotificationTemplateRow, ...] = await self.repository.versions(session, name)
        builtin: BuiltinTemplate | None = builtin_named(name)
        if not rows and builtin is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "template not found")
        active_version: int | None = _active_version(rows, builtin)
        versions: list[TemplateVersionInfo] = []
        if builtin is not None:
            versions.append(TemplateVersionInfo(0, active_version == 0, "system", None))
        versions.extend(
            TemplateVersionInfo(row.version, row.active, row.created_by, row.created_at)
            for row in rows
        )
        event: NotificationEvent = _event_of(rows, builtin)
        return TemplateDetail(
            name,
            event,
            builtin is not None,
            active_version,
            tuple(versions),
            allowed_paths(event),
        )

    async def version_text(
        self, session: AsyncSession, name: str, version: int
    ) -> TemplateVersionView:
        """Return the text of one version (0 is the built-in), or raise not_found."""
        rows: tuple[NotificationTemplateRow, ...] = await self.repository.versions(session, name)
        builtin: BuiltinTemplate | None = builtin_named(name)
        if version == 0 and builtin is not None:
            return TemplateVersionView(
                name,
                0,
                builtin.event_kind,
                _active_version(rows, builtin) == 0,
                builtin.texts,
            )
        row: NotificationTemplateRow | None = next(
            (candidate for candidate in rows if candidate.version == version), None
        )
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "template version not found")
        return TemplateVersionView(
            name, row.version, NotificationEvent(row.event_kind), row.active, texts_of(row)
        )

    async def validate(
        self,
        session: AsyncSession,
        event_kind: NotificationEvent,
        texts: TemplateTexts,
        name: str | None = None,
    ) -> tuple[TemplateIssue, ...]:
        """Return the validation issues of a candidate version; empty means valid."""
        existing: NotificationEvent | None = (
            await self._existing_event(session, name) if name is not None else None
        )
        return validate_template(name, event_kind, texts, existing, self.redactor)

    async def preview(self, event: NotificationEvent, texts: TemplateTexts) -> PreviewResult:
        """Render both channels with the synthetic context, then redact and gate them.

        Raises `TemplateValidationError` when the texts are invalid. Never reads data.
        """
        issues: tuple[TemplateIssue, ...] = validate_template(
            None, event, texts, None, self.redactor
        )
        if issues:
            raise TemplateValidationError(issues)
        return PreviewResult(
            await self._preview_variant(NotificationChannel.EMAIL, event, texts),
            await self._preview_variant(NotificationChannel.SLACK, event, texts),
        )

    async def create_version(
        self,
        session: AsyncSession,
        name: str,
        event_kind: NotificationEvent,
        texts: TemplateTexts,
        actor_ref: str,
    ) -> tuple[str, int]:
        """Store a new inactive version and audit it.

        On validation failure, append a `template.rejected` audit record (field
        names and codes only) to the caller transaction and raise
        `TemplateValidationError`; nothing else is written. The caller must commit
        that transaction if the rejection record should persist.
        """
        issues: tuple[TemplateIssue, ...] = await self.validate(session, event_kind, texts, name)
        if issues:
            await self.audit.append(
                session,
                _entry(
                    actor_ref,
                    "template.rejected",
                    "invalid" if any(issue.field == "name" for issue in issues) else name,
                    "Notification template version rejected",
                    json_object(
                        (
                            (
                                "fields",
                                json_array(tuple(json_str(issue.field) for issue in issues)),
                            ),
                            (
                                "codes",
                                json_array(tuple(json_str(issue.code.value) for issue in issues)),
                            ),
                        )
                    ),
                ),
            )
            raise TemplateValidationError(issues)
        version: int = await self.repository.latest_version(session, name) + 1
        await self.repository.insert(
            session,
            NotificationTemplateRow(
                id=new_id(),
                name=name,
                version=version,
                event_kind=event_kind.value,
                description=texts.description,
                subject=texts.subject,
                body=texts.body,
                email_subject=texts.email_subject,
                email_body=texts.email_body,
                slack_body=texts.slack_body,
                active=False,
                created_by=actor_ref[:80],
                created_at=self.clock.now(),
            ),
        )
        await self.audit.append(
            session,
            _entry(
                actor_ref,
                "template.created",
                name,
                "Notification template version created",
                _version_details(name, version, event_kind),
            ),
        )
        return name, version

    async def activate(
        self, session: AsyncSession, name: str, version: int, actor_ref: str
    ) -> None:
        """Make one version active (0 restores the built-in) under row locks, and audit."""
        rows: tuple[NotificationTemplateRow, ...] = await self.repository.versions(
            session, name, lock=True
        )
        builtin: BuiltinTemplate | None = builtin_named(name)
        if version == 0:
            if builtin is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "template version not found")
        elif all(row.version != version for row in rows):
            raise TelescreenError(ErrorCode.NOT_FOUND, "template version not found")
        for row in rows:
            row.active = row.version == version
        await session.flush()
        await self.audit.append(
            session,
            _entry(
                actor_ref,
                "template.activated",
                name,
                "Notification template version activated",
                _version_details(name, version, _event_of(rows, builtin)),
            ),
        )

    async def lookup(self, session: AsyncSession, name: str) -> TemplateInfo | None:
        """Return the event kind and active version of a name (`TemplateCatalog`)."""
        rows: tuple[NotificationTemplateRow, ...] = await self.repository.versions(session, name)
        builtin: BuiltinTemplate | None = builtin_named(name)
        if not rows and builtin is None:
            return None
        return TemplateInfo(name, _event_of(rows, builtin), _active_version(rows, builtin))

    async def _existing_event(self, session: AsyncSession, name: str) -> NotificationEvent | None:
        """Return the event kind already bound to a name, if any."""
        rows: tuple[NotificationTemplateRow, ...] = await self.repository.versions(session, name)
        if rows:
            return NotificationEvent(rows[0].event_kind)
        builtin: BuiltinTemplate | None = builtin_named(name)
        return builtin.event_kind if builtin is not None else None

    async def _preview_variant(
        self, channel: NotificationChannel, event: NotificationEvent, texts: TemplateTexts
    ) -> PreviewVariant:
        """Render one channel variant and pass it through the redactor and DLP gate."""
        subject: str
        body: str
        subject, body = render_for_channel(texts, channel, synthetic_context(event), event)
        safe: CensoredMessage = await self.gate.inspect_redacted(OutboundMessage(subject, body))
        return PreviewVariant(safe.subject, safe.body, safe.verdict)


def _event_of(
    rows: tuple[NotificationTemplateRow, ...], builtin: BuiltinTemplate | None
) -> NotificationEvent:
    """Return a name's event kind from its stored versions or its built-in."""
    if rows:
        return NotificationEvent(rows[0].event_kind)
    if builtin is None:
        raise TelescreenError(ErrorCode.NOT_FOUND, "template not found")
    return builtin.event_kind


def _active_version(
    rows: tuple[NotificationTemplateRow, ...], builtin: BuiltinTemplate | None
) -> int | None:
    """Return the active stored version, else 0 for a built-in, else None."""
    active: NotificationTemplateRow | None = next((row for row in rows if row.active), None)
    if active is not None:
        return active.version
    return 0 if builtin is not None else None


def _version_details(name: str, version: int, event: NotificationEvent) -> JsonValue:
    """Return audit details limited to the name, version and event kind."""
    return json_object(
        (
            ("name", json_str(name)),
            ("version", json_int(version)),
            ("event_kind", json_str(event.value)),
        )
    )


def _entry(actor_ref: str, action: str, name: str, summary: str, details: JsonValue) -> AuditEntry:
    """Build a template audit entry for a member or system actor."""
    return AuditEntry(
        ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.SYSTEM,
        actor_ref,
        action,
        "notification_template",
        name,
        summary,
        details,
    )
