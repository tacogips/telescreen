"""Command handlers for v1.2 notification and identity utilities."""

import sys

from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json, json_null, json_object, json_str
from telescreen.core.permissions import parse_role
from telescreen.core.vocabulary import (
    ApiScope,
    EngineTaskKind,
    ErrorCode,
    NotificationChannel,
    Role,
    RoleChangeSource,
)
from telescreen.db.models.notifications import NotificationRow
from telescreen.directory.tokens import CreatedToken
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.templates.service import TemplateInfo
from telescreen.notifications.templates.synthetic import synthetic_context
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.ports.workflow import ClaimedTask
from telescreen.wiring import Container


async def notify_test(
    container: Container, channel_name: str, recipient: str, name: str | None
) -> int:
    """Queue and deliver one synthetic notification through production services."""
    try:
        channel: NotificationChannel = NotificationChannel(channel_name)
    except ValueError:
        print("invalid notification channel", file=sys.stderr)
        return 2
    template_name: str = name or "system.workflow_notify"
    async with container.sessions.begin() as session:
        template: TemplateInfo | None = await container.v12.templates.lookup(session, template_name)
        if template is None:
            print("unknown template", file=sys.stderr)
            return 2
        request: NotificationRequest = NotificationRequest(
            channel,
            (recipient,),
            "",
            "",
            "system:cli",
            event=template.event_kind,
            template_name=template_name,
            context=synthetic_context(template.event_kind),
            record_unreachable=True,
        )
        receipt: NotificationReceipt = await container.v12.notifications.send(session, request)
    for notification_id in receipt.notification_ids:
        task: ClaimedTask = ClaimedTask(
            notification_id,
            EngineTaskKind.NOTIFICATION_DELIVERY,
            notification_id,
            json_null(),
            1,
            1,
            f"notify:{notification_id}",
        )
        await container.v12.delivery.handle(task)
    identifiers: tuple[str, ...] = receipt.notification_ids + receipt.skipped
    rows: tuple[NotificationRow, ...] = await _notification_rows(container, identifiers)
    for row in rows:
        print(
            dump_json(
                json_object(
                    (
                        ("notification_id", json_str(row.id)),
                        ("channel", json_str(row.channel)),
                        ("status", json_str(row.status)),
                        ("dlp_verdict", json_str(row.dlp_verdict)),
                        (
                            "error_code",
                            json_str(row.error_code) if row.error_code is not None else json_null(),
                        ),
                    )
                )
            )
        )
    return 0 if rows and all(row.status == "sent" for row in rows) else 1


async def _notification_rows(
    container: Container, identifiers: tuple[str, ...]
) -> tuple[NotificationRow, ...]:
    """Load safe delivery metadata for the CLI result rows."""
    repository: NotificationRepository = NotificationRepository()
    rows: list[NotificationRow] = []
    async with container.sessions() as session:
        for identifier in identifiers:
            row: NotificationRow | None = await repository.get(session, identifier)
            if row is not None:
                rows.append(row)
    return tuple(rows)


async def member_set_role(container: Container, handle: str, role_text: str) -> int:
    """Change a member role through the audited role service."""
    try:
        role: Role | None = parse_role(role_text)
        async with container.sessions.begin() as session:
            await container.v12.roles.set_role(
                session, handle, role, "system:cli", RoleChangeSource.CLI
            )
    except TelescreenError as error:
        if error.code is ErrorCode.LAST_ADMIN:
            print("last admin cannot be removed", file=sys.stderr)
        else:
            print("member role could not be changed", file=sys.stderr)
        return 1
    return 0


async def create_token(
    container: Container,
    label: str,
    scopes: frozenset[ApiScope],
    member_handle: str | None,
) -> int:
    """Create a token, optionally bound to a resolved member handle."""
    try:
        async with container.sessions.begin() as session:
            member_id: str | None = (
                await container.token_service.member_id_for_handle(session, member_handle)
                if member_handle is not None
                else None
            )
            created: CreatedToken = await container.token_service.create(
                session, label, scopes, member_id
            )
    except TelescreenError as error:
        if error.code is ErrorCode.NOT_FOUND and member_handle is not None:
            print("unknown member", file=sys.stderr)
            return 2
        raise
    print(created.token)
    return 0
