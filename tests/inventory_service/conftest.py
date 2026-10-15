"""Assemble two independent applications connected only through HTTP contracts."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from telescreen.api.app import create_app
from telescreen.core.clock import FakeClock
from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.vocabulary import ApiScope
from telescreen.db.ownership import Store
from telescreen.directory.tokens import CreatedToken
from telescreen.inventory_service.app import (
    InventoryRuntime,
    build_inventory_runtime,
    create_inventory_app,
)
from telescreen.wiring import Container, build_container, close_container
from tests.e2e.harness import PairEnv
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class ServiceNetwork(httpx.AsyncBaseTransport):
    """Route HTTP by host, with injectable outages and lost committed replies."""

    apps: tuple[tuple[str, FastAPI], ...] = ()
    blocked: set[str] = field(default_factory=set)
    lose_reply: set[str] = field(default_factory=set)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """All cross-owner calls traverse the actual application's HTTP handlers."""
        if request.url.host in self.blocked:
            raise httpx.ConnectError("unavailable")
        app: FastAPI | None = next(
            (app for host, app in self.apps if host == request.url.host), None
        )
        if app is None:
            raise AssertionError(f"unexpected host {request.url.host}")
        response: httpx.Response = await httpx.ASGITransport(app=app).handle_async_request(request)
        if request.url.path in self.lose_reply:
            self.lose_reply.remove(request.url.path)
            await response.aread()
            raise httpx.ReadError("reply lost")
        return response


@dataclass(frozen=True)
class RemoteWorld:
    """Independent stores, applications, clocks and authenticated user clients."""

    main: SqliteHarness
    inventory: SqliteHarness
    container: Container
    runtime: InventoryRuntime
    network: ServiceNetwork
    client: httpx.AsyncClient
    clock: FakeClock
    admin: str
    viewer: str
    inventory_app: FastAPI

    async def tick(self) -> None:
        """Run each owner's work independently without a cross-owner transaction."""
        await self.container.worker.run_until_idle()
        await self.runtime.worker.publish_due()
        await self.runtime.worker.audit_tick()
        await self.runtime.worker.retention_tick()


@asynccontextmanager
async def remote_services(
    tmp_path: Path,
    main: SqliteHarness,
    inventory: SqliteHarness,
    main_url: str = "sqlite+aiosqlite:///:memory:",
    inventory_url: str = "sqlite+aiosqlite:///:memory:",
    configured: bool = False,
) -> AsyncGenerator[RemoteWorld, None]:
    """The bureau gets no inventory URL; inventory gets no main URL."""
    network: ServiceNetwork = ServiceNetwork()
    http: httpx.AsyncClient = httpx.AsyncClient(transport=network)
    clock: FakeClock = FakeClock(datetime(2026, 10, 15, 9, tzinfo=UTC))
    common: tuple[tuple[str, str], ...] = (
        ("TELESCREEN_FINGERPRINT_KEY", "env:FP"),
        ("FP", "f" * 32),
        ("TELESCREEN_INVENTORY_SERVICE_TOKEN", "env:SERVICE_TOKEN"),
        ("SERVICE_TOKEN", "test-service-secret"),
        ("TELESCREEN_DATA_OVERDUE_CRON", "* * * * *"),
        ("TELESCREEN_EVIDENCE_DIR", str(tmp_path / "evidence")),
    )
    bureau_env: PairEnv = PairEnv(
        common
        + (
            ("TELESCREEN_DATABASE_URL", main_url),
            ("TELESCREEN_INVENTORY_SERVICE_URL", "http://inventory"),
        )
    )
    inventory_env: PairEnv = PairEnv(
        common
        + (
            ("TELESCREEN_INVENTORY_DATABASE_URL", inventory_url),
            ("TELESCREEN_BUREAU_SERVICE_URL", "http://bureau"),
        )
    )
    settings: TelescreenSettings = load_settings(bureau_env)
    container: Container = build_container(
        settings, bureau_env, http, None if configured else main.sessions, clock
    )
    container.worker.concurrency = 1
    runtime: InventoryRuntime = build_inventory_runtime(
        load_settings(inventory_env),
        inventory_env,
        http,
        None if configured else inventory.sessions,
        clock,
    )
    inventory_app: FastAPI = create_inventory_app(runtime)
    network.apps = (("bureau", create_app(container)), ("inventory", inventory_app))
    admin: CreatedToken
    viewer: CreatedToken
    async with main.sessions.begin() as session:
        admin = await container.token_service.create(
            session, "admin", frozenset((ApiScope.ADMIN, ApiScope.READ, ApiScope.WRITE)), None
        )
        viewer = await container.token_service.create(
            session, "viewer", frozenset((ApiScope.READ,)), None
        )
    client: httpx.AsyncClient = httpx.AsyncClient(transport=network, base_url="http://bureau")
    try:
        yield RemoteWorld(
            main,
            inventory,
            container,
            runtime,
            network,
            client,
            clock,
            admin.token,
            viewer.token,
            inventory_app,
        )
    finally:
        await client.aclose()
        await close_container(container)
        await http.aclose()
        if runtime.engine is not None:
            await runtime.engine.dispose()


@pytest.fixture
async def remote_world(tmp_path: Path) -> AsyncGenerator[RemoteWorld, None]:
    """Run SQLite flows with independently owned physical databases."""
    main: SqliteHarness = await make_sqlite_harness(tmp_path / "main.db", store=Store.MAIN)
    inventory: SqliteHarness = await make_sqlite_harness(
        tmp_path / "inventory.db", store=Store.INVENTORY
    )
    try:
        async with remote_services(tmp_path, main, inventory) as world:
            yield world
    finally:
        await inventory.close()
        await main.close()
