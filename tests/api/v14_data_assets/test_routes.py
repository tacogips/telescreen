"""Router construction tests for the p04 asset API permission contract."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from httpx import Response as HttpResponse
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import install_error_handlers
from telescreen.api.common.auth import PermissionDependency, Principal
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.data_asset_detail import (
    DataAssetDetailRouterDeps,
    build_data_asset_detail_router,
)
from telescreen.api.routers.data_assets import DataAssetsRouterDeps, build_data_assets_router
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, parse_json
from telescreen.core.permissions import Permission, role_permissions
from telescreen.core.vocabulary import (
    ActorDisplayKind,
    ErrorCode,
    PrincipalKind,
    Role,
)
from telescreen.db.models import DataAssetRow
from telescreen.inventory.assets import AssetService
from telescreen.inventory.export import AssetExporter
from telescreen.inventory.queries import AssetQueries
from telescreen.inventory.repository import InventoryRepository
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import ActorDirectory, ActorDisplay
from telescreen.ports.issues import ResourceIssueReader, ResourceIssueRef
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, github_token


class EmptyIssueReader(ResourceIssueReader):
    """Return no open issues for router construction tests."""

    async def open_issues_for_resources(
        self, session: AsyncSession, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Return an empty issue projection."""
        del session, resource_keys
        return ()


class EmptyActorDirectory(ActorDirectory):
    """Return unknown safe displays for router construction tests."""

    async def display(
        self, session: AsyncSession, refs: tuple[str, ...]
    ) -> tuple[ActorDisplay, ...]:
        """Return one unknown display per requested reference."""
        del session
        return tuple(
            ActorDisplay(ref, ActorDisplayKind.UNKNOWN, "Unknown", None, False) for ref in refs
        )


class EmptyAudit(AuditLog):
    """Accept audit writes without side effects in route construction tests."""

    entries: list[AuditEntry]

    def __init__(self) -> None:
        self.entries = []

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Return a stable sequence placeholder."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass(frozen=True)
class RolePermissionProvider:
    """Apply the selected role's real permission set in ASGI route tests."""

    role: Role
    permission: Permission

    async def __call__(self) -> Principal:
        """Return a principal or the same forbidden outcome as the real guard."""
        permissions: frozenset[Permission] = role_permissions(self.role)
        if self.permission not in permissions:
            raise TelescreenError(ErrorCode.FORBIDDEN, "permission is not granted")
        return Principal(
            PrincipalKind.OIDC,
            None,
            "route-test-member",
            self.role,
            permissions,
        )


def _authorized_app(routers: tuple[APIRouter, ...], role: Role) -> FastAPI:
    """Build a local app and override each declared guard with role semantics."""
    app: FastAPI = FastAPI()
    for router in routers:
        app.include_router(router)
    install_error_handlers(app)
    for router in routers:
        for route in router.routes:
            if isinstance(route, APIRoute):
                for dependency in route.dependant.dependencies:
                    if isinstance(dependency.call, PermissionDependency):
                        app.dependency_overrides[dependency.call] = RolePermissionProvider(
                            role, dependency.call.permission
                        )
    return app


def _asset(asset_id: str, number: int, origin: str, at: datetime) -> DataAssetRow:
    """Build an asset row for route mutation assertions."""
    return DataAssetRow(
        id=asset_id,
        number=number,
        resource_key=f"{number:064x}",
        source="custom",
        origin=origin,
        kind="other",
        state="present",
        name=asset_id,
        display_locator=asset_id,
        collector_config_id=None,
        sweep_covered=False,
        item_count=5,
        first_seen_at=at,
        last_seen_at=at,
        deleted_at=None,
        overdue_notified_for=None,
        created_by=None,
        created_at=at,
        updated_at=at,
    )


@pytest.mark.asyncio
async def test_p04_routes_have_one_expected_permission_each() -> None:
    """The four collection and eight detail routes have exactly one permission."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
        audit: EmptyAudit = EmptyAudit()
        clock: FakeClock = FakeClock(now)
        ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
        actors: EmptyActorDirectory = EmptyActorDirectory()
        issues: EmptyIssueReader = EmptyIssueReader()
        queries: AssetQueries = AssetQueries(issues, actors)
        redactor: Redactor = Redactor.default()
        repository: InventoryRepository = InventoryRepository()
        exporter: AssetExporter = AssetExporter(queries, redactor)
        assets: AssetService = AssetService(repository, audit, redactor, clock)
        collection_router: APIRouter = build_data_assets_router(
            DataAssetsRouterDeps(ctx, queries, exporter, audit, redactor)
        )
        detail_router: APIRouter = build_data_asset_detail_router(
            DataAssetDetailRouterDeps(ctx, queries, assets)
        )
        routes: tuple[APIRoute, ...] = tuple(
            route
            for route in (*collection_router.routes, *detail_router.routes)
            if isinstance(route, APIRoute)
        )
        expected: tuple[Permission, ...] = (
            Permission.DATA_READ,
            Permission.DATA_READ,
            Permission.DATA_READ,
            Permission.DATA_READ,
            Permission.DATA_READ,
            Permission.DATA_READ,
            Permission.DATA_READ,
            Permission.DATA_READ,
            Permission.DATA_WRITE,
            Permission.DATA_WRITE,
            Permission.DATA_WRITE,
            Permission.DATA_WRITE,
        )
        assert len(routes) == 12
        for route, permission in zip(routes, expected, strict=True):
            dependencies: tuple[PermissionDependency, ...] = tuple(
                dependency.call
                for dependency in route.dependant.dependencies
                if isinstance(dependency.call, PermissionDependency)
            )
            assert len(dependencies) == 1
            assert dependencies[0].permission is permission
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_p04_api_statuses_redaction_writes_and_response_shape() -> None:
    """Asset endpoints enforce roles, avoid secret echoes, and audit successful writes."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    audit: EmptyAudit = EmptyAudit()
    clock: FakeClock = FakeClock(now)
    ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
    queries: AssetQueries = AssetQueries(EmptyIssueReader(), EmptyActorDirectory())
    redactor: Redactor = Redactor.default()
    repository: InventoryRepository = InventoryRepository()
    exporter: AssetExporter = AssetExporter(queries, redactor)
    assets: AssetService = AssetService(repository, audit, redactor, clock)
    routers: tuple[APIRouter, ...] = (
        build_data_assets_router(DataAssetsRouterDeps(ctx, queries, exporter, audit, redactor)),
        build_data_asset_detail_router(DataAssetDetailRouterDeps(ctx, queries, assets)),
    )
    sample: Sample = github_token()
    try:
        async with harness.sessions.begin() as session:
            session.add_all(
                (
                    _asset("discovered", 1, "discovered", now),
                    _asset("registered", 2, "registered", now),
                )
            )
        viewer_app: FastAPI = _authorized_app(routers, Role.VIEWER)
        async with AsyncClient(
            transport=ASGITransport(app=viewer_app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            read_response: HttpResponse = await client.get("/api/data/assets")
            assert read_response.status_code == 200
            exported: HttpResponse = await client.get("/api/data/export")
            assert exported.status_code == 200
            assert exported.headers["content-type"].startswith("text/csv")
            assert exported.headers["x-telescreen-truncated"] == "false"
            denied_write: HttpResponse = await client.patch(
                "/api/data/assets/registered", json={"kind": "table"}
            )
            assert denied_write.status_code == 403

        app: FastAPI = _authorized_app(routers, Role.ADMIN)
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            unknown: HttpResponse = await client.get("/api/data/assets/missing")
            assert unknown.status_code == 404
            bad_key: HttpResponse = await client.get("/api/data/resource-assets/ABC")
            assert bad_key.status_code == 422
            missing: HttpResponse = await client.get(f"/api/data/resource-assets/{'f' * 64}")
            assert missing.status_code == 200
            missing_payload: JsonValue = parse_json(missing.text)
            missing_asset: JsonValue | None = missing_payload.get("asset")
            assert missing_asset is not None and missing_asset.kind is JsonKind.NULL
            found: HttpResponse = await client.get(f"/api/data/resource-assets/{2:064x}")
            assert found.status_code == 200
            found_payload: JsonValue = parse_json(found.text)
            found_asset: JsonValue | None = found_payload.get("asset")
            assert found_asset is not None and found_asset.kind is JsonKind.OBJECT
            secret_query: HttpResponse = await client.get(
                "/api/data/assets", params={"q": sample.value}
            )
            assert secret_query.status_code == 422
            assert sample.value not in secret_query.text

            discovered_patch: HttpResponse = await client.patch(
                "/api/data/assets/discovered", json={"name": "Renamed"}
            )
            assert discovered_patch.status_code == 422
            future: HttpResponse = await client.post(
                "/api/data/assets/registered/events",
                json={
                    "occurred_at": (now + timedelta(minutes=6)).isoformat(),
                    "items_added": 1,
                },
            )
            assert future.status_code == 422
            secret_note: HttpResponse = await client.post(
                "/api/data/assets/registered/events",
                json={"occurred_at": now.isoformat(), "note": sample.value},
            )
            assert secret_note.status_code == 422
            assert sample.value not in secret_note.text

            renamed: HttpResponse = await client.patch(
                "/api/data/assets/registered", json={"name": "Updated"}
            )
            assert renamed.status_code == 200
            response: JsonValue = parse_json(renamed.text)
            assert {member.name for member in response.members} == {
                "id",
                "short_id",
                "number",
                "name",
                "source",
                "display_locator",
                "kind",
                "origin",
                "state",
                "item_count",
                "item_trend",
                "open_issue_count",
                "owner",
                "deletion_due",
                "overdue",
                "incomplete",
                "first_seen_at",
                "last_seen_at",
                "deleted_at",
                "collector_config_id",
                "sweep_covered",
                "created_by",
                "value_count",
                "fields",
                "missing_required",
                "open_issues",
            }
            manual: HttpResponse = await client.post(
                "/api/data/assets/registered/events",
                json={
                    "occurred_at": now.isoformat(),
                    "items_added": 3,
                    "items_removed": 1,
                },
            )
            assert manual.status_code == 201
            async with harness.sessions.begin() as session:
                registered: DataAssetRow | None = await session.get(DataAssetRow, "registered")
                assert registered is not None and registered.item_count == 7
            deleted: HttpResponse = await client.post(
                "/api/data/assets/registered/mark-deleted", json={}
            )
            assert deleted.status_code == 200
            deleted_twice: HttpResponse = await client.post(
                "/api/data/assets/registered/mark-deleted", json={}
            )
            assert deleted_twice.status_code == 409
            restored: HttpResponse = await client.post("/api/data/assets/registered/restore")
            assert restored.status_code == 200
            restored_twice: HttpResponse = await client.post("/api/data/assets/registered/restore")
            assert restored_twice.status_code == 409

        assert tuple(entry.action for entry in audit.entries) == (
            "data_asset.exported",
            "data_asset.updated",
            "data_asset.event_recorded",
            "data_asset.marked_deleted",
            "data_asset.restored",
        )
    finally:
        await harness.close()
