"""Notification adapters for the full split service topology."""

from dataclasses import dataclass

import httpx
from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.collectors.inventory_publish import InventoryPublisher
from telescreen.core.clock import Clock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource, SecretValue, resolve_secret
from telescreen.directory.owners import DirectoryOwnerResolver
from telescreen.directory.resolver import DirectoryRecipientResolver
from telescreen.integrations.notification_client import (
    ChannelStatusClient,
    NotificationCommandClient,
    RemoteNotificationTester,
    TemplateCatalogClient,
)
from telescreen.integrations.notification_delivery import (
    NotificationCommandDeliveryHandler,
    NotificationDeliveryNoop,
    NotificationRecoveryProvider,
)
from telescreen.integrations.notification_outbox import NotificationOutbox
from telescreen.integrations.notification_publish import (
    NotificationOutboxPublisher,
    OutboxMemberNotifier,
    OutboxNotificationSender,
)
from telescreen.inventory_service.bureau import build_proxy_router
from telescreen.inventory_service.transport import NOTIFICATION_PREFIX, ServiceHttp
from telescreen.masking.redact import Redactor
from telescreen.ports.notifications import MemberNotifier, NotificationSender, NotificationTester
from telescreen.ports.templates import TemplateCatalog
from telescreen.ports.workflow import ScheduleProvider, TaskHandler, TaskQueue
from telescreen.projections.markers import install_projection_markers
from telescreen.projections.relay import ProjectionRelayHandler, ProjectionRelayProvider
from telescreen.wiring_inventory_parts import NotificationParts

NOTIFICATION_PROXY_PATHS: tuple[str, ...] = (
    "/api/auth/me/notification-settings",
    "/api/notification-templates",
    "/api/notification-templates/{path:path}",
)


@dataclass(frozen=True)
class SplitNotificationComposition:
    """Notification ports plus the durable producer outbox used by split mode."""

    parts: NotificationParts
    delivery: NotificationCommandDeliveryHandler
    publisher: NotificationOutboxPublisher


@dataclass(frozen=True)
class SplitNotificationProducers:
    """Producer ports available before the remote Inventory publisher is wired."""

    sender: NotificationSender
    members: MemberNotifier
    publisher: NotificationOutboxPublisher


def build_split_notification_producers(
    recipients: DirectoryRecipientResolver,
    owners: DirectoryOwnerResolver,
    redactor: Redactor,
    queue: TaskQueue,
    clock: Clock,
) -> SplitNotificationProducers:
    """Create producer outbox adapters without resolving service credentials."""
    outbox: NotificationOutbox = NotificationOutbox()
    publisher: NotificationOutboxPublisher = NotificationOutboxPublisher(outbox, queue, clock)
    return SplitNotificationProducers(
        OutboxNotificationSender(recipients, redactor, publisher),
        OutboxMemberNotifier(owners, redactor, publisher),
        publisher,
    )


def build_split_notifications(
    settings: TelescreenSettings,
    env: EnvSource,
    http: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    clock: Clock,
    queue: TaskQueue,
    redactor: Redactor,
    producers: SplitNotificationProducers,
    inventory: InventoryPublisher,
) -> SplitNotificationComposition:
    """Bind split-mode producers and transports without resolving delivery secrets."""
    service_url: str | None = settings.notification_service_url
    token_ref: str | None = settings.notification_service_token_ref
    if service_url is None or token_ref is None:
        raise ConfigError("notification service URL and token are required in split mode")
    token: SecretValue = resolve_secret(token_ref, env)
    if not token.value.strip():
        raise ConfigError("notification service token must not be empty")
    transport: ServiceHttp = ServiceHttp(http, service_url, token, NOTIFICATION_PREFIX)
    proxy_transport: ServiceHttp = ServiceHttp(http, service_url, token, "")
    publisher: NotificationOutboxPublisher = producers.publisher
    outbox: NotificationOutbox = publisher.outbox
    sender: NotificationSender = producers.sender
    members: MemberNotifier = producers.members
    catalog: TemplateCatalog = TemplateCatalogClient(transport)
    tester: NotificationTester = RemoteNotificationTester(transport)
    channel_status: ChannelStatusClient = ChannelStatusClient(transport)
    delivery: NotificationCommandDeliveryHandler = NotificationCommandDeliveryHandler(
        sessions, outbox, NotificationCommandClient(transport), clock
    )
    relay: ProjectionRelayHandler = ProjectionRelayHandler(
        sessions, inventory, publisher, queue, clock
    )
    install_projection_markers(sessions, clock)
    routers: tuple[APIRouter, ...] = (
        build_proxy_router(proxy_transport, NOTIFICATION_PROXY_PATHS),
    )
    handlers: tuple[TaskHandler, ...] = (
        delivery,
        NotificationDeliveryNoop(),
        relay,
    )
    providers: tuple[ScheduleProvider, ...] = (
        NotificationRecoveryProvider(outbox, queue),
        ProjectionRelayProvider(queue),
    )
    return SplitNotificationComposition(
        NotificationParts(
            sender,
            members,
            catalog,
            tester,
            channel_status,
            routers,
            handlers,
            providers,
        ),
        delivery,
        publisher,
    )
