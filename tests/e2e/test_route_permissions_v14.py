"""Exact permission table and viewer denial checks for v1.4 data routes."""

from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient, MockTransport

from telescreen.api.app import create_app
from telescreen.api.common import PermissionDependency
from telescreen.core.config import TelescreenSettings
from telescreen.core.permissions import Permission
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import ApiScope
from telescreen.directory.tokens import CreatedToken
from telescreen.wiring import Container, build_container, close_container
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class _TestEnv(EnvSource):
    """Provide the local fingerprint secret required by container assembly."""

    def get(self, key: str, /) -> str | None:
        """Resolve only the test key."""
        return "v" * 32 if key == "TELESCREEN_TEST_KEY" else None


def _route_rows(
    permission: Permission, routes: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str, Permission], ...]:
    """Associate each method and path pair with its documented permission."""
    return tuple((method, path, permission) for method, path in routes)


def _expected() -> tuple[tuple[str, str, Permission], ...]:
    """Return the exact 22 v1.4 routes from design section 9."""
    read: tuple[tuple[str, str], ...] = (
        ("GET", "/api/data/assets"),
        ("GET", "/api/data/counts"),
        ("GET", "/api/data/export"),
        ("GET", "/api/data/resource-assets/{resource_key}"),
        ("GET", "/api/data/assets/{asset_id}"),
        ("GET", "/api/data/assets/{asset_id}/events"),
        ("GET", "/api/data/assets/{asset_id}/items"),
        ("GET", "/api/data/assets/{asset_id}/item-series"),
        ("GET", "/api/data/fields"),
    )
    writes: tuple[tuple[str, str], ...] = (
        ("POST", "/api/data/assets"),
        ("PATCH", "/api/data/assets/{asset_id}"),
        ("PUT", "/api/data/assets/{asset_id}/fields"),
        ("POST", "/api/data/assets/{asset_id}/events"),
        ("POST", "/api/data/assets/{asset_id}/mark-deleted"),
        ("POST", "/api/data/assets/{asset_id}/restore"),
        ("POST", "/api/data/fields/validate-values"),
    )
    admin: tuple[tuple[str, str], ...] = (
        ("POST", "/api/data/fields"),
        ("PATCH", "/api/data/fields/{field_id}"),
        ("DELETE", "/api/data/fields/{field_id}"),
        ("POST", "/api/data/fields/{field_id}/archive"),
        ("POST", "/api/data/fields/{field_id}/unarchive"),
        ("POST", "/api/data/fields/reorder"),
    )
    return (
        _route_rows(Permission.DATA_READ, read)
        + _route_rows(Permission.DATA_WRITE, writes)
        + _route_rows(Permission.DATA_FIELDS_ADMIN, admin)
    )


def _permissions(dependant: Dependant) -> tuple[Permission, ...]:
    """Collect permission declarations recursively from route dependencies."""
    found: list[Permission] = []
    for dependency in dependant.dependencies:
        if isinstance(dependency.call, PermissionDependency):
            found.append(dependency.call.permission)
        found.extend(_permissions(dependency))
    return tuple(found)


@pytest.mark.asyncio
async def test_v14_routes_match_exact_permission_table() -> None:
    """Require exactly one expected permission marker on each of 22 routes."""
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
        expected: tuple[tuple[str, str, Permission], ...] = _expected()
        actual: list[tuple[str, str, Permission]] = []
        for router in container.v14.routers:
            for route in router.routes:
                if not isinstance(route, APIRoute):
                    continue
                permissions: tuple[Permission, ...] = _permissions(route.dependant)
                for method in route.methods or ():
                    assert len(permissions) == 1, f"{method} {route.path}: {permissions}"
                    actual.append((method, route.path, permissions[0]))
        assert len(expected) == 22
        assert tuple(sorted(actual, key=lambda row: (row[1], row[0]))) == tuple(
            sorted(expected, key=lambda row: (row[1], row[0]))
        )

        async with database.sessions.begin() as session:
            viewer: CreatedToken = await container.token_service.create(
                session, "v14-viewer", frozenset((ApiScope.READ,)), None
            )
        app: FastAPI = create_app(container)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers: tuple[tuple[str, str], ...] = (("Authorization", f"Bearer {viewer.token}"),)
            read: httpx.Response = await client.get("/api/data/counts", headers=headers)
            write: httpx.Response = await client.post(
                "/api/data/assets/example/events",
                headers=headers,
                json={"occurred_at": "2026-10-13T00:00:00Z"},
            )
            admin: httpx.Response = await client.post(
                "/api/data/fields/reorder", headers=headers, json={"ids": []}
            )
        assert read.status_code == 200, read.text
        assert write.status_code == 403, write.text
        assert admin.status_code == 403, admin.text
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()
