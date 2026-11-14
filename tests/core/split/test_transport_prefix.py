"""Tests for configurable service transport prefixes."""

import httpx
from pydantic import TypeAdapter

from telescreen.core.secrets import SecretValue
from telescreen.inventory_service.transport import NOTIFICATION_PREFIX, PREFIX, ServiceHttp


async def test_service_http_uses_configured_prefix() -> None:
    """A notification client can share transport code with its own route prefix."""
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Record the requested path and return a typed string body."""
        paths.append(request.url.path)
        return httpx.Response(200, json="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        transport: ServiceHttp = ServiceHttp(
            http, "http://notification", SecretValue("placeholder"), NOTIFICATION_PREFIX
        )
        result: str = await transport.post("/test", "{}", TypeAdapter(str))
    assert result == "ok"
    assert paths == ["/internal/notification/v1/test"]
    assert PREFIX == "/internal/inventory/v1"
