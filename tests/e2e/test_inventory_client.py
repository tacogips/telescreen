"""Telescreen-to-Inventory command client contract tests."""

from datetime import UTC, datetime

import httpx
import pytest

from telescreen.core.errors import TelescreenError
from telescreen.core.secrets import SecretValue
from telescreen.integrations.inventory_client import InventoryClient
from telescreen.inventory_service.transport import ServiceHttp
from telescreen.ports.inventory_commands import InventoryCommand, InventoryReceipt, StopTracking


async def test_inventory_client_posts_command_and_validates_receipt() -> None:
    """Post one encoded command with the service credential and decode its receipt."""
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json="applied")

    command: InventoryCommand = InventoryCommand(
        "event-1",
        "stream-1",
        1,
        datetime(2026, 10, 16, tzinfo=UTC),
        StopTracking("collector-1", "system:test"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        client: InventoryClient = InventoryClient(
            ServiceHttp(http, "http://inventory", SecretValue("service-secret"))
        )
        receipt: InventoryReceipt = await client.receive(command)

    assert receipt is InventoryReceipt.APPLIED
    assert len(requests) == 1
    assert requests[0].url.path == "/internal/inventory/v1/commands"
    assert requests[0].headers["x-telescreen-service-token"] == "service-secret"
    assert b'"event_id":"event-1"' in requests[0].content


async def test_inventory_client_rejects_malformed_receipt() -> None:
    """Never acknowledge an unrecognized inventory response body."""
    command: InventoryCommand = InventoryCommand(
        "event-1",
        "stream-1",
        1,
        datetime(2026, 10, 16, tzinfo=UTC),
        StopTracking("collector-1", "system:test"),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    ) as http:
        client: InventoryClient = InventoryClient(
            ServiceHttp(http, "http://inventory", SecretValue("service-secret"))
        )
        with pytest.raises(TelescreenError):
            await client.receive(command)
