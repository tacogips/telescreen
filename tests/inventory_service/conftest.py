"""Isolated inventory runtime fixtures with a fake Notification HTTP peer."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from telescreen.api.common.auth import hash_token
from telescreen.core.clock import FakeClock
from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.vocabulary import ApiScope
from telescreen.db.ownership import Store
from telescreen.inventory_service.app import (
    InventoryRuntime,
    build_inventory_runtime,
    create_inventory_app,
)
from telescreen.ports.directory_projection import (
    ApiTokenEntity,
    DirectoryEntity,
    DirectoryEntityKind,
)
from telescreen.ports.notification_commands import NotificationCommandReceipt
from tests.e2e.harness import PairEnv
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class ServiceNetwork(httpx.AsyncBaseTransport):
    """Route inventory requests to its ASGI app and answer Notification calls."""

    inventory_app: FastAPI | None = None
    requests: list[str] | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Keep every call local and record the requested origin/path."""
        if self.requests is None:
            self.requests = []
        self.requests.append(str(request.url))
        if request.url.host == "notification":
            return httpx.Response(200, json=NotificationCommandReceipt.APPLIED.value)
        if request.url.host != "inventory" or self.inventory_app is None:
            raise AssertionError("unexpected service destination")
        return await httpx.ASGITransport(app=self.inventory_app).handle_async_request(request)


@dataclass(frozen=True)
class InventoryWorld:
    """One inventory store/runtime and its authenticated local HTTP client."""

    harness: SqliteHarness
    runtime: InventoryRuntime
    client: httpx.AsyncClient
    network: ServiceNetwork
    clock: FakeClock
    token: str

    @property
    def headers(self) -> tuple[tuple[str, str], ...]:
        """Return both user and process credentials required by the app."""
        return (
            ("authorization", f"Bearer {self.token}"),
            ("x-telescreen-service-token", "inventory-service-secret"),
        )


@pytest.fixture
async def inventory_world(tmp_path: Path) -> AsyncGenerator[InventoryWorld, None]:
    """Build inventory without importing or composing any Telescreen application."""
    harness: SqliteHarness = await make_sqlite_harness(
        tmp_path / "inventory.db", store=Store.INVENTORY
    )
    clock: FakeClock = FakeClock(datetime(2026, 10, 15, 9, tzinfo=UTC))
    env: PairEnv = PairEnv(
        (
            ("TELESCREEN_INVENTORY_DATABASE_URL", "sqlite+aiosqlite:///:memory:"),
            ("TELESCREEN_INVENTORY_SERVICE_TOKEN", "env:INVENTORY_TOKEN"),
            ("INVENTORY_TOKEN", "inventory-service-secret"),
            ("TELESCREEN_NOTIFICATION_SERVICE_URL", "http://notification"),
            ("TELESCREEN_NOTIFICATION_INVENTORY_TOKEN", "env:NOTIFICATION_TOKEN"),
            ("NOTIFICATION_TOKEN", "notification-inventory-secret"),
            ("TELESCREEN_FINGERPRINT_KEY", "env:FINGERPRINT"),
            ("FINGERPRINT", "f" * 32),
            ("TELESCREEN_EVIDENCE_DIR", str(tmp_path / "evidence")),
        )
    )
    settings: TelescreenSettings = load_settings(env)
    network: ServiceNetwork = ServiceNetwork()
    http: httpx.AsyncClient = httpx.AsyncClient(transport=network)
    runtime: InventoryRuntime = build_inventory_runtime(
        settings, env, http, harness.sessions, clock
    )
    app: FastAPI = create_inventory_app(runtime)
    network.inventory_app = app
    token: str = "inventory-test-token"
    entity: ApiTokenEntity = ApiTokenEntity(
        "token-id",
        "test token",
        hash_token(token),
        (ApiScope.ADMIN.value, ApiScope.READ.value, ApiScope.WRITE.value),
        None,
        False,
    )
    async with harness.sessions.begin() as session:
        await runtime.replica.apply(
            session,
            DirectoryEntity(DirectoryEntityKind.API_TOKEN, entity.id, 1, True, entity),
        )
    client: httpx.AsyncClient = httpx.AsyncClient(transport=network, base_url="http://inventory")
    try:
        yield InventoryWorld(harness, runtime, client, network, clock, token)
    finally:
        await client.aclose()
        await http.aclose()
        if runtime.engine is not None:
            await runtime.engine.dispose()
        await harness.close()
