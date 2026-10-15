"""Typed HTTP clients for Telescreen-to-Notification synchronous operations."""

from dataclasses import dataclass

import httpx
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, NotificationChannel
from telescreen.inventory_service.transport import NOTIFICATION_PREFIX, ServiceHttp
from telescreen.ports.notification_commands import (
    ChannelStatus,
    NotificationCommand,
    NotificationCommandReceipt,
    TestRequest,
    encode_notification_command,
)
from telescreen.ports.notifications import TestOutcome
from telescreen.ports.templates import TemplateCatalog, TemplateInfo


@dataclass(frozen=True)
class NotificationCommandClient:
    """Publish Telescreen commands to Notification over the fixed service origin."""

    transport: ServiceHttp

    async def receive(self, command: NotificationCommand) -> NotificationCommandReceipt:
        """Return a validated durable command receipt."""
        return await self.transport.post(
            "/commands",
            encode_notification_command(command),
            TypeAdapter(NotificationCommandReceipt),
        )


@dataclass(frozen=True)
class TemplateCatalogClient(TemplateCatalog):
    """Implement template lookup without sharing the caller's database session."""

    transport: ServiceHttp

    async def lookup(self, session: AsyncSession, name: str) -> TemplateInfo | None:
        """Look up one typed template metadata record remotely."""
        del session
        return await self.transport.post(
            "/templates/lookup",
            TypeAdapter(str).dump_json(name).decode(),
            TypeAdapter(TemplateInfo | None),
        )


@dataclass(frozen=True)
class RemoteNotificationTester:
    """Request a channel test from the Notification owner."""

    transport: ServiceHttp

    async def send_test(self, channel: NotificationChannel, member_id: str) -> TestOutcome:
        """Return the typed safe outcome from the remote test-send operation."""
        return await self.transport.post(
            "/test",
            TypeAdapter(TestRequest).dump_json(TestRequest(channel, member_id)).decode(),
            TypeAdapter(TestOutcome),
        )


@dataclass(frozen=True)
class ChannelStatusClient:
    """Read configured email and Slack channel flags from Notification."""

    transport: ServiceHttp

    async def status(self) -> ChannelStatus:
        """Return the remote configured channel flags."""
        response: httpx.Response = await self.transport.request(
            "GET", NOTIFICATION_PREFIX + "/channels"
        )
        if response.status_code != 200:
            raise TelescreenError(ErrorCode.SERVICE_UNAVAILABLE, "service unavailable")
        try:
            return TypeAdapter(ChannelStatus).validate_json(response.content, strict=True)
        except ValueError:
            raise TelescreenError(
                ErrorCode.SERVICE_UNAVAILABLE, "invalid service response"
            ) from None
