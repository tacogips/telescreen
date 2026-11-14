"""End-to-end checks for v1.4 service wiring and static route resolution."""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient, MockTransport

from telescreen.api.app import create_app
from telescreen.collectors.service import CollectorService
from telescreen.collectors.tasks import CollectorScanHandler
from telescreen.core.config import TelescreenSettings
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import ApiScope, EngineTaskKind
from telescreen.directory.tokens import CreatedToken
from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.reconcile import InventoryReconcilerImpl
from telescreen.inventory.retention import DataOverdueHandler, DataOverdueProvider
from telescreen.ports.workflow import TaskHandler
from telescreen.wiring import Container, build_container, close_container
from telescreen.wiring_v14 import WiringV14
from telescreen.workflow.mapreduce.reduce import MapReduceHandler
from tests.support.db import SqliteHarness, make_sqlite_harness


class _TestEnv(EnvSource):
    """Provide the local fingerprint secret required by container assembly."""

    def get(self, key: str, /) -> str | None:
        """Resolve only the test key."""
        return "v" * 32 if key == "TELESCREEN_TEST_KEY" else None


@pytest.mark.asyncio
async def test_v14_wiring_binds_services_and_resolves_static_routes() -> None:
    """Bind real inventory hooks and keep static data paths out of dynamic routes."""
    database: SqliteHarness = await make_sqlite_harness()
    outbound: httpx.AsyncClient = httpx.AsyncClient(
        transport=MockTransport(lambda request: httpx.Response(200, request=request))
    )
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
    )
    container: Container = build_container(settings, _TestEnv(), outbound, database.sessions)
    try:
        assert isinstance(container.v14, WiringV14)
        assert isinstance(container.v14.observer, InventoryObserverImpl)
        assert isinstance(container.v14.reconciler, InventoryReconcilerImpl)
        assert container.issue_service.inventory is container.v14.publisher
        assert isinstance(container.collector, CollectorService)
        assert container.collector.inventory is container.v14.publisher
        scan_handler: TaskHandler | None = container.worker.registry.get(
            EngineTaskKind.COLLECTOR_SCAN
        )
        reduce_handler: TaskHandler | None = container.worker.registry.get(
            EngineTaskKind.MAP_REDUCE
        )
        assert isinstance(scan_handler, CollectorScanHandler)
        assert scan_handler.inventory is container.v14.publisher
        assert isinstance(reduce_handler, MapReduceHandler)
        assert reduce_handler.inventory is container.v14.publisher
        assert any(
            isinstance(item, DataOverdueProvider) for item in container.worker.scheduler.providers
        )
        assert (
            container.worker.registry.get(EngineTaskKind.DATA_OVERDUE_CHECK)
            is container.v14.overdue_handler
        )
        assert isinstance(container.v14.overdue_handler, DataOverdueHandler)
        assert (
            container.worker.registry.get(EngineTaskKind.INVENTORY_DELIVERY)
            is container.v14.delivery_handler
        )
        assert container.v14.recovery_provider in container.worker.scheduler.providers
        assert container.v14.audit_recovery_provider in container.worker.scheduler.providers
        assert (
            container.worker.registry.get(EngineTaskKind.INVENTORY_AUDIT_DELIVERY)
            is container.v14.audit_delivery_handler
        )
        assert container.v14.assets.audit is container.v14.audit_publisher
        assert container.v14.fields.audit is container.v14.audit_publisher
        assert container.v14.values.audit is container.v14.audit_publisher
        assert container.v14.registration.audit is container.v14.audit_publisher
        assert container.v14.retention_recovery_provider in container.worker.scheduler.providers
        assert (
            container.worker.registry.get(EngineTaskKind.RETENTION_DELIVERY)
            is container.v14.retention_delivery_handler
        )

        app: FastAPI = create_app(container)
        routes: tuple[APIRoute, ...] = tuple(
            route
            for router in container.v14.routers
            for route in router.routes
            if isinstance(route, APIRoute)
        )
        expected: tuple[tuple[str, str, str], ...] = (
            ("GET", "/api/data/counts", "counts"),
            ("GET", "/api/data/export", "export_assets"),
            ("GET", "/api/data/resource-assets/{resource_key}", "asset_for_resource"),
            ("POST", "/api/data/fields/reorder", "reorder_fields"),
        )
        for method, path, endpoint in expected:
            matches: tuple[APIRoute, ...] = tuple(
                route for route in routes if route.path == path and method in (route.methods or ())
            )
            assert len(matches) == 1
            assert matches[0].name == endpoint
        async with database.sessions.begin() as session:
            token: CreatedToken = await container.token_service.create(
                session, "v14-wiring", frozenset((ApiScope.ADMIN,)), None
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers: tuple[tuple[str, str], ...] = (("Authorization", f"Bearer {token.token}"),)
            counts: httpx.Response = await client.get("/api/data/counts", headers=headers)
            exported: httpx.Response = await client.get("/api/data/export", headers=headers)
            resource: httpx.Response = await client.get(
                "/api/data/resource-assets/" + "a" * 64, headers=headers
            )
            reordered: httpx.Response = await client.post(
                "/api/data/fields/reorder",
                headers=headers,
                json={"ids": []},
            )
            assert counts.status_code == 200, counts.text
            assert exported.status_code == 200, exported.text
            assert resource.status_code == 200, resource.text
            assert resource.json() == {"asset": None}
            assert reordered.status_code == 200, reordered.text
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()
