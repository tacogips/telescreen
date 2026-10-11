"""Transaction-bound rendering, DLP, audit, and notification task enqueue service."""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, json_bool, json_int, json_null, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
    NotificationStatus,
)
from telescreen.db.models.notifications import NotificationRow
from telescreen.notifications.addresses import channel_addresses
from telescreen.notifications.dlp import CensoredMessage, DlpGate, OutboundMessage
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.templates.builtins import builtin_name
from telescreen.notifications.templates.model import ResolvedTemplate
from telescreen.notifications.templates.render import render_for_channel
from telescreen.notifications.templates.resolver import BuiltinTemplateResolver, TemplateResolver
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import RecipientResolver, ResolvedRecipients
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationReceipt,
    NotificationRequest,
)
from telescreen.ports.workflow import TaskQueue, TaskSpec

_ACTIONS: tuple[tuple[NotificationStatus, str], ...] = (
    (NotificationStatus.QUEUED, "notification.queued"),
    (NotificationStatus.BLOCKED, "notification.blocked"),
    (NotificationStatus.SKIPPED, "notification.skipped"),
)


@dataclass(frozen=True)
class _Rendered:
    """The gated message of one request and the template that produced it."""

    message: CensoredMessage
    template_name: str | None = None
    template_version: int | None = None
    fallback: bool = False


@dataclass(frozen=True)
class NotificationService:
    """Render, gate, persist, audit, and enqueue notifications in one transaction.

    Requests without an event keep the v1 inline behavior. Requests with an event
    render a template (or, with `inline`, use the supplied text), then pass the
    rule redactor and the DLP gate before anything is stored.
    """

    gate: DlpGate
    resolver: RecipientResolver
    queue: TaskQueue
    audit: AuditLog
    clock: Clock
    repository: NotificationRepository
    templates: TemplateResolver = field(default_factory=BuiltinTemplateResolver)

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Check recipients, render and gate once, then write one row per recipient."""
        problems: tuple[ErrorCode | None, ...] = await self._check_recipients(session, request)
        rendered: _Rendered = await self._render(session, request)
        safe_message: CensoredMessage = rendered.message
        notification_ids: list[str] = []
        skipped_ids: list[str] = []
        for recipient_ref, problem in zip(request.recipients, problems, strict=True):
            notification_id: str = new_id()
            status: NotificationStatus = NotificationStatus.QUEUED
            if problem is not None:
                status = NotificationStatus.SKIPPED
                skipped_ids.append(notification_id)
            else:
                notification_ids.append(notification_id)
                if safe_message.verdict is DlpVerdict.BLOCKED:
                    status = NotificationStatus.BLOCKED
            row: NotificationRow = NotificationRow(
                id=notification_id,
                channel=request.channel.value,
                recipient_ref=recipient_ref,
                subject=safe_message.subject,
                body=safe_message.body,
                dlp_verdict=safe_message.verdict.value,
                dlp_hit_count=safe_message.hit_count,
                status=status.value,
                delivery_attempts=0,
                run_id=request.run_id,
                issue_id=request.issue_id,
                created_at=self.clock.now(),
                sent_at=None,
                event_kind=request.event.value if request.event is not None else None,
                template_name=rendered.template_name,
                template_version=rendered.template_version,
                set_name=request.set_name,
                target_index=request.target_index,
                error_code=problem.value if problem is not None else None,
            )
            await self.repository.insert(session, row)
            action: str = next(pair[1] for pair in _ACTIONS if pair[0] is status)
            await self.audit.append(
                session,
                AuditEntry(
                    ActorKind.SYSTEM,
                    request.actor_ref,
                    action,
                    "notification",
                    notification_id,
                    action,
                    _details(request, rendered, problem),
                ),
            )
            if status is NotificationStatus.QUEUED:
                await self.queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.NOTIFICATION_DELIVERY,
                        notification_id,
                        json_null(),
                        self.clock.now(),
                        f"notify:{notification_id}",
                        5,
                    ),
                )
        return NotificationReceipt(
            tuple(notification_ids),
            safe_message.verdict,
            bool(notification_ids) and safe_message.verdict is not DlpVerdict.BLOCKED,
            tuple(skipped_ids),
        )

    async def _check_recipients(
        self, session: AsyncSession, request: NotificationRequest
    ) -> tuple[ErrorCode | None, ...]:
        """Resolve each ref; record or raise unknown refs depending on the request."""
        problems: list[ErrorCode | None] = []
        for recipient_ref in request.recipients:
            if not request.record_unreachable:
                await self.resolver.resolve(session, recipient_ref)
                problems.append(None)
                continue
            try:
                resolved: ResolvedRecipients = await self.resolver.resolve(session, recipient_ref)
            except TelescreenError as error:
                if error.code is not ErrorCode.RECIPIENT_UNKNOWN:
                    raise
                problems.append(ErrorCode.RECIPIENT_UNKNOWN)
                continue
            reachable: bool = bool(channel_addresses(resolved, request.channel))
            problems.append(None if reachable else ErrorCode.RECIPIENT_UNREACHABLE)
        return tuple(problems)

    async def _render(self, session: AsyncSession, request: NotificationRequest) -> _Rendered:
        """Produce the gated message for the v1, inline-event, or template path."""
        message: OutboundMessage = OutboundMessage(request.subject, request.body)
        if request.event is None:
            return _Rendered(await self.gate.inspect(message))
        if request.inline:
            return _Rendered(await self.gate.inspect_redacted(message))
        template: ResolvedTemplate = await self.templates.resolve(
            session, request.template_name or builtin_name(request.event), request.event
        )
        subject: str
        body: str
        subject, body = render_for_channel(
            template.texts,
            request.channel,
            request.context if request.context is not None else NotificationContext(),
            request.event,
        )
        return _Rendered(
            await self.gate.inspect_redacted(OutboundMessage(subject, body)),
            template.name,
            template.version,
            template.fallback,
        )


def _details(
    request: NotificationRequest, rendered: _Rendered, problem: ErrorCode | None
) -> JsonValue:
    """Return safe audit details: enums, counts, names and indexes that are set."""
    members: list[tuple[str, JsonValue]] = [
        ("channel", json_str(request.channel.value)),
        ("verdict", json_str(rendered.message.verdict.value)),
        ("hit_count", json_int(rendered.message.hit_count)),
    ]
    if problem is not None:
        members.append(("error_code", json_str(problem.value)))
    if request.event is not None:
        members.append(("event_kind", json_str(request.event.value)))
    if rendered.template_name is not None:
        members.append(("template_name", json_str(rendered.template_name)))
    if rendered.template_version is not None:
        members.append(("template_version", json_int(rendered.template_version)))
    if request.set_name is not None:
        members.append(("set_name", json_str(request.set_name)))
    if request.target_index is not None:
        members.append(("target_index", json_int(request.target_index)))
    if rendered.fallback:
        members.append(("template_fallback", json_bool(True)))
    return json_object(members)
