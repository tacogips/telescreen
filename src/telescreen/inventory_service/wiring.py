"""Bureau composition when inventory is a separately deployed owner."""

from dataclasses import dataclass

from fastapi import APIRouter

from telescreen.collectors.inventory_publish import InventoryPublisher
from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource, SecretValue, resolve_secret
from telescreen.integrations.inventory_client import InventoryClient
from telescreen.integrations.inventory_delivery import (
    InventoryDeliveryHandler,
    InventoryRecoveryProvider,
)
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.inventory_service.bureau import build_proxy_router
from telescreen.inventory_service.transport import ServiceHttp
from telescreen.ports.workflow import ScheduleProvider, TaskHandler
from telescreen.wiring_inventory_parts import WiringV14Parts


@dataclass(frozen=True)
class RemoteInventoryWiring:
    """Only publisher delivery, recovery and public API forwarding run in core."""

    publisher: InventoryPublisher
    delivery_handler: InventoryDeliveryHandler
    recovery_provider: InventoryRecoveryProvider
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
    routers: tuple[APIRouter, ...] = (build_proxy_router(transport),)
    return RemoteInventoryWiring(publisher, delivery, recovery, routers, (recovery,), (delivery,))


def service_secret(ref: str | None, env: EnvSource) -> SecretValue:
    """Fail before opening resources when remote authentication is unconfigured."""
    if not ref:
        raise ConfigError("inventory service token is required")
    secret: SecretValue = resolve_secret(ref, env)
    if not secret.value.strip():
        raise ConfigError("inventory service token must not be empty")
    return secret
