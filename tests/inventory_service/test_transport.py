"""Failure boundaries never acknowledge unvalidated remote success."""

import gzip
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import TypeAdapter

from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import ErrorCode
from telescreen.inventory_service.clients import InventoryClient
from telescreen.inventory_service.transport import ServiceHttp
from telescreen.ports.inventory_commands import InventoryCommand, InventoryReceipt, StopTracking


@pytest.mark.parametrize(
    "body,status",
    [(b'"invented"', 200), (b"{}", 200), (b'"applied"', 302), (b"sensitive upstream error", 500)],
)
async def test_invalid_receipts_and_failures_are_safe(body: bytes, status: int) -> None:
    """A misleading success, redirect or upstream error cannot become an acknowledgment."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body))
    ) as http:
        client: InventoryClient = InventoryClient(
            ServiceHttp(http, "http://inventory", SecretValue("secret"))
        )
        with pytest.raises(TelescreenError) as failure:
            await client.receive(
                InventoryCommand(
                    "event",
                    "collector:config",
                    1,
                    datetime(2026, 10, 15, tzinfo=UTC),
                    StopTracking("config", "system:test"),
                )
            )
        assert failure.value.code is ErrorCode.SERVICE_UNAVAILABLE
        assert "sensitive" not in str(failure.value)


async def test_transport_uses_timeout_service_auth_and_does_not_follow_redirects() -> None:
    """Fixed-origin authenticated calls never redirect their secret to another server."""

    def reply(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-telescreen-service-token"] == "secret"
        assert request.extensions["timeout"] is not None
        return httpx.Response(307, headers={"location": "http://attacker"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reply), follow_redirects=True
    ) as http:
        transport: ServiceHttp = ServiceHttp(http, "http://inventory", SecretValue("secret"))
        with pytest.raises(TelescreenError):
            await transport.post("/commands", "{}", TypeAdapter(InventoryReceipt))


async def test_compressed_upstream_response_is_decoded_once() -> None:
    """A decoded response must not retain a stale content-encoding header."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=gzip.compress(b'"applied"'), headers={"content-encoding": "gzip"}
            )
        )
    ) as http:
        transport: ServiceHttp = ServiceHttp(http, "http://inventory", SecretValue("secret"))
        assert (
            await transport.post("/commands", "{}", TypeAdapter(InventoryReceipt))
            is InventoryReceipt.APPLIED
        )


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/test",
        "http://user:password@inventory",
        "http://inventory/path",
        "http://inventory?query=yes",
        "http://inventory#fragment",
    ],
)
def test_invalid_service_origins_fail_without_opening_a_connection(url: str) -> None:
    """Credentials and ambiguous URL components are rejected at startup."""
    with pytest.raises(ConfigError):
        ServiceHttp(httpx.AsyncClient(), url, SecretValue("secret"))


async def test_public_exports_stream_beyond_the_internal_handoff_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The internal message bound must not truncate or reject existing public CSV exports."""
    from fastapi import FastAPI

    from telescreen.inventory_service import transport as boundary
    from telescreen.inventory_service.bureau import build_proxy_router

    monkeypatch.setattr(boundary, "MAX_HANDOFF_BYTES", 32)
    payload: bytes = b"csv,row\n" * 1000
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=payload,
                headers={
                    "content-type": "text/csv",
                    "content-disposition": "attachment; filename=assets.csv",
                },
            )
        )
    ) as upstream:
        app: FastAPI = FastAPI()
        app.include_router(
            build_proxy_router(ServiceHttp(upstream, "http://inventory", SecretValue("secret")))
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://bureau"
        ) as public:
            response: httpx.Response = await public.get("/api/data/export")
            assert response.status_code == 200
            assert response.content == payload
            assert response.headers["content-disposition"] == "attachment; filename=assets.csv"
