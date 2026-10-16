"""Lazy, single-process notification composition."""

from dataclasses import dataclass
from typing import cast

import httpx
from fastapi import APIRouter

from telescreen.api.common.context import ApiContext
from telescreen.api.routers.notification_settings import (
    NotificationSettingsRouterDeps,
    build_notification_settings_router,
)
from telescreen.core.config import TelescreenSettings
from telescreen.core.secrets import EnvSource, SecretValue, resolve_secret
from telescreen.directory.owners import DirectoryOwnerResolver
from telescreen.directory.resolver import DirectoryRecipientResolver
from telescreen.integrations.status import ChannelStatusSource
from telescreen.notifications.delivery import NotificationDeliveryHandler
from telescreen.notifications.dlp import DlpGate
from telescreen.notifications.email import EmailNotifier, SmtplibTransport
from telescreen.notifications.preferences import PreferenceRepository, PreferenceService
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.notifications.slack import SlackNotifier
from telescreen.notifications.tester import NotificationServiceTester
from telescreen.ports.notification_commands import ChannelStatus
from telescreen.ports.notifications import NotificationTester
from telescreen.ports.templates import TemplateCatalog
from telescreen.ports.workflow import TaskHandler, TaskQueue
from telescreen.wiring_builders import RuntimePrimitives
from telescreen.wiring_inventory_parts import NotificationParts


@dataclass(frozen=True)
class LocalChannelStatusSource(ChannelStatusSource):
    """Expose safe local channel configuration through the shared port."""

    settings: TelescreenSettings

    async def status(self) -> ChannelStatus:
        """Return channel flags without resolving secret references."""
        return ChannelStatus(
            bool(self.settings.smtp_host and self.settings.smtp_from),
            bool(self.settings.slack_bot_token_ref),
        )


@dataclass(frozen=True)
class LocalNotificationComposition:
    """Concrete local sender and delivery task exposed through typed parts."""

    parts: NotificationParts
    service: NotificationService
    delivery: NotificationDeliveryHandler


def build_local_notifications(
    settings: TelescreenSettings,
    env: EnvSource,
    http: httpx.AsyncClient,
    primitives: RuntimePrimitives,
    context: ApiContext,
    recipients: DirectoryRecipientResolver,
    queue: TaskQueue,
    gate: DlpGate,
    catalog: TemplateCatalog,
) -> LocalNotificationComposition:
    """Build every database-bound notification adapter for single-process mode."""
    from telescreen.notifications.templates.repository import TemplateRepository
    from telescreen.notifications.templates.resolver import DbTemplateResolver

    template_resolver: DbTemplateResolver = DbTemplateResolver(TemplateRepository())
    slack_token: SecretValue | None = (
        resolve_secret(settings.slack_bot_token_ref, env)
        if settings.slack_bot_token_ref is not None
        else None
    )
    smtp_password: SecretValue | None = (
        resolve_secret(settings.smtp_password_ref, env)
        if settings.smtp_password_ref is not None
        else None
    )
    service: NotificationService = NotificationService(
        gate,
        recipients,
        queue,
        primitives.audit,
        primitives.clock,
        NotificationRepository(),
        template_resolver,
    )
    delivery: NotificationDeliveryHandler = NotificationDeliveryHandler(
        primitives.sessions,
        recipients,
        SlackNotifier(http, slack_token),
        EmailNotifier(
            SmtplibTransport(
                settings.smtp_host or "localhost",
                settings.smtp_port,
                settings.smtp_username,
                smtp_password,
                settings.smtp_starttls,
            ),
            settings.smtp_from or "telescreen@localhost",
        ),
        primitives.audit,
        primitives.clock,
        NotificationRepository(),
    )
    preferences: PreferenceService = PreferenceService(PreferenceRepository(), primitives.clock)
    tester: NotificationTester = NotificationServiceTester(
        primitives.sessions, service, delivery, DirectoryOwnerResolver()
    )
    channel_status: LocalChannelStatusSource = LocalChannelStatusSource(settings)
    routers: tuple[APIRouter, ...] = (
        build_notification_settings_router(NotificationSettingsRouterDeps(context, preferences)),
    )
    parts: NotificationParts = NotificationParts(
        service,
        service,
        catalog,
        tester,
        channel_status,
        routers,
        (cast(TaskHandler, delivery),),
        (),
    )
    return LocalNotificationComposition(parts, service, delivery)
