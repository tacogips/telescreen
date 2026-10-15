"""Bureau composition when inventory is a separately deployed owner."""

from dataclasses import dataclass

from fastapi import APIRouter

from telescreen.audit.receiver import AuditCommandReceiver
from telescreen.audit.service import AuditLogService
from telescreen.collectors.inventory_publish import InventoryPublisher
from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource, SecretValue, resolve_secret
from telescreen.directory.inventory_actors import InventoryActorDirectory
from telescreen.directory.inventory_context import IdentityActorLookup, IdentityAssigneeLookup
from telescreen.integrations.inventory_delivery import (
    InventoryDeliveryHandler,
    InventoryRecoveryProvider,
)
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.inventory_service.bureau import (
    BureauEndpoints,
    build_bureau_router,
    build_proxy_router,
)
from telescreen.inventory_service.clients import InventoryClient
from telescreen.inventory_service.transport import InternalAuth, ServiceHttp
from telescreen.issues.asset_context import IssueAssetLookup, IssueAssetOwnerLookup
from telescreen.issues.asset_owner import IssueAssetOwnerResolver
from telescreen.notifications.retention import RetentionNotificationReceiver
from telescreen.ports.workflow import ScheduleProvider, TaskHandler
from telescreen.wiring_inventory_parts import WiringV14Parts


@dataclass(frozen=True)
class RemoteInventoryWiring:
    """Only producer delivery and bureau-owned callbacks run in the bureau."""

    publisher: InventoryPublisher
    delivery_handler: InventoryDeliveryHandler
    recovery_provider: InventoryRecoveryProvider
    retention_receiver: RetentionNotificationReceiver
    routers: tuple[APIRouter, ...]
    providers: tuple[ScheduleProvider, ...]
    handlers: tuple[TaskHandler, ...]


def build_remote_inventory(parts: WiringV14Parts, transport: ServiceHttp) -> RemoteInventoryWiring:
    """Never construct inventory services or open its database in remote mode."""
    client: InventoryClient = InventoryClient(transport)
    outbox: InventoryOutbox = InventoryOutbox()
    publisher: InventoryPublisher = InventoryPublisher(outbox, parts.queue, parts.clock)
    delivery: InventoryDeliveryHandler = InventoryDeliveryHandler(
        parts.sessions, outbox, client, parts.clock
    )
    recovery: InventoryRecoveryProvider = InventoryRecoveryProvider(outbox, parts.queue)
    audit: AuditLogService = AuditLogService(parts.clock, parts.redactor)
    retention: RetentionNotificationReceiver = RetentionNotificationReceiver(
        parts.sessions,
        client,
        parts.members,
        parts.sender,
        parts.recipients,
        IssueAssetOwnerLookup(parts.sessions, IssueAssetOwnerResolver(parts.owners)),
        audit,
        parts.clock,
    )
    endpoints: BureauEndpoints = BureauEndpoints(
        parts.context,
        IssueAssetLookup(parts.sessions, parts.issue_reader),
        IdentityActorLookup(
            parts.sessions, InventoryActorDirectory(parts.actors, parts.collector_configs)
        ),
        IdentityAssigneeLookup(parts.sessions, parts.recipients),
        AuditCommandReceiver(parts.sessions, audit),
        retention,
    )
    routers: tuple[APIRouter, ...] = (
        build_proxy_router(transport),
        build_bureau_router(endpoints, InternalAuth(transport.secret)),
    )
    return RemoteInventoryWiring(
        publisher, delivery, recovery, retention, routers, (recovery,), (delivery,)
    )


def service_secret(ref: str | None, env: EnvSource) -> SecretValue:
    """Fail before opening resources when remote authentication is unconfigured."""
    if not ref:
        raise ConfigError("inventory service token is required")
    secret: SecretValue = resolve_secret(ref, env)
    if not secret.value.strip():
        raise ConfigError("inventory service token must not be empty")
    return secret
