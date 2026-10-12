"""Exact route permission table for Telescreen v1.3 endpoints."""

from dataclasses import dataclass

import httpx
import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import MockTransport

from telescreen.api.common import PermissionDependency
from telescreen.core.config import TelescreenSettings
from telescreen.core.permissions import Permission
from telescreen.core.secrets import EnvSource
from telescreen.wiring import Container, build_container, close_container
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class _TestEnv(EnvSource):
    """Provide the local fingerprint secret required by container assembly."""

    def get(self, key: str, /) -> str | None:
        """Resolve only the test key."""
        return "r" * 32 if key == "TELESCREEN_TEST_KEY" else None


def _route_rows(
    permission: Permission, routes: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str, Permission], ...]:
    """Associate each method and path pair with its documented permission."""
    return tuple((method, path, permission) for method, path in routes)


def _expected() -> tuple[tuple[str, str, Permission], ...]:
    """Return the v1.3 endpoint table from design-api-and-ui.md."""
    issues_read: tuple[tuple[str, str], ...] = (
        ("GET", "/api/issues"),
        ("GET", "/api/issues/counts"),
        ("GET", "/api/issues/{issue_id}"),
        ("GET", "/api/issues/{issue_id}/events"),
        ("GET", "/api/issues/{issue_id}/events/{selector}"),
        ("GET", "/api/issues/{issue_id}/tags"),
        ("GET", "/api/issues/{issue_id}/stats"),
        ("GET", "/api/issues/{issue_id}/activity"),
        ("GET", "/api/issues/{issue_id}/external-issues"),
        ("GET", "/api/external-issues/github/repos"),
        ("GET", "/api/dashboard"),
    )
    views_read: tuple[tuple[str, str], ...] = (("GET", "/api/views"),)
    views_write: tuple[tuple[str, str], ...] = (
        ("POST", "/api/views"),
        ("PATCH", "/api/views/{view_id}"),
        ("DELETE", "/api/views/{view_id}"),
        ("PUT", "/api/views/{view_id}/star"),
        ("DELETE", "/api/views/{view_id}/star"),
        ("PUT", "/api/views/default"),
    )
    issues_write: tuple[tuple[str, str], ...] = (
        ("POST", "/api/issues/{issue_id}/archive"),
        ("POST", "/api/issues/{issue_id}/unarchive"),
        ("POST", "/api/issues/{issue_id}/review"),
        ("PATCH", "/api/issues/{issue_id}"),
        ("POST", "/api/issues/bulk"),
        ("POST", "/api/issues/merge"),
        ("POST", "/api/issues/{issue_id}/unmerge"),
        ("POST", "/api/issues/{issue_id}/comments"),
        ("PATCH", "/api/issues/{issue_id}/comments/{comment_id}"),
        ("DELETE", "/api/issues/{issue_id}/comments/{comment_id}"),
    )
    follow: tuple[tuple[str, str], ...] = (
        ("PUT", "/api/issues/{issue_id}/subscription"),
        ("PUT", "/api/issues/{issue_id}/bookmark"),
        ("DELETE", "/api/issues/{issue_id}/bookmark"),
    )
    external_write: tuple[tuple[str, str], ...] = (
        ("POST", "/api/issues/{issue_id}/external-issues"),
        ("DELETE", "/api/issues/{issue_id}/external-issues/{link_id}"),
        ("POST", "/api/issues/{issue_id}/external-issues/github"),
    )
    alerts_read: tuple[tuple[str, str], ...] = (
        ("GET", "/api/alert-rules"),
        ("GET", "/api/alert-rules/{rule_id}"),
        ("GET", "/api/alert-rules/{rule_id}/history"),
    )
    alerts_write: tuple[tuple[str, str], ...] = (
        ("POST", "/api/alert-rules"),
        ("POST", "/api/alert-rules/validate"),
        ("PUT", "/api/alert-rules/{rule_id}"),
        ("DELETE", "/api/alert-rules/{rule_id}"),
        ("POST", "/api/alert-rules/{rule_id}/enable"),
        ("POST", "/api/alert-rules/{rule_id}/disable"),
    )
    alerts_mute: tuple[tuple[str, str], ...] = (
        ("POST", "/api/alert-rules/{rule_id}/mute"),
        ("POST", "/api/alert-rules/{rule_id}/unmute"),
    )
    profile: tuple[tuple[str, str], ...] = (
        ("GET", "/api/auth/me/notification-settings"),
        ("PUT", "/api/auth/me/notification-settings"),
        ("GET", "/api/auth/me/tokens"),
        ("POST", "/api/auth/me/tokens"),
        ("POST", "/api/auth/me/tokens/{token_id}/revoke"),
    )
    expected: tuple[tuple[str, str, Permission], ...] = (
        _route_rows(Permission.ISSUES_READ, issues_read)
        + _route_rows(Permission.VIEWS_READ, views_read)
        + _route_rows(Permission.VIEWS_WRITE, views_write)
        + _route_rows(Permission.ISSUES_WRITE, issues_write)
        + _route_rows(Permission.ISSUES_FOLLOW, follow)
        + _route_rows(Permission.EXTERNAL_ISSUES_WRITE, external_write)
        + _route_rows(Permission.ALERTS_READ, alerts_read)
        + _route_rows(Permission.ALERTS_WRITE, alerts_write)
        + _route_rows(Permission.ALERTS_MUTE, alerts_mute)
        + _route_rows(Permission.PROFILE_SELF, profile)
        + _route_rows(Permission.INTEGRATIONS_READ, (("GET", "/api/integrations"),))
        + _route_rows(Permission.INTEGRATIONS_TEST, (("POST", "/api/integrations/{kind}/test"),))
        + _route_rows(Permission.DIRECTORY_WRITE, (("PATCH", "/api/members/{handle}"),))
        + _route_rows(
            Permission.USERS_WRITE,
            (
                ("POST", "/api/members/{handle}/deactivate"),
                ("POST", "/api/members/{handle}/reactivate"),
            ),
        )
        + _route_rows(
            Permission.OWNERSHIP_READ,
            (
                ("GET", "/api/ownership-rules"),
                ("POST", "/api/ownership-rules/test"),
            ),
        )
        + _route_rows(
            Permission.OWNERSHIP_WRITE,
            (
                ("POST", "/api/ownership-rules"),
                ("PUT", "/api/ownership-rules/{rule_id}"),
                ("DELETE", "/api/ownership-rules/{rule_id}"),
                ("POST", "/api/ownership-rules/reorder"),
            ),
        )
        + _route_rows(Permission.WORKFLOWS_READ, (("GET", "/api/workflows"),))
    )
    # The repeated read rows are intentionally deduplicated by endpoint, not permission.
    return expected


def _permissions(dependant: Dependant) -> tuple[Permission, ...]:
    """Collect permission declarations recursively from route dependencies."""
    found: list[Permission] = []
    for dependency in dependant.dependencies:
        if isinstance(dependency.call, PermissionDependency):
            found.append(dependency.call.permission)
        found.extend(_permissions(dependency))
    return tuple(found)


@pytest.mark.asyncio
async def test_v13_routes_match_documented_permissions() -> None:
    """Require every documented v1.3 route to declare its single permission."""
    database: SqliteHarness = await make_sqlite_harness()
    outbound: httpx.AsyncClient = httpx.AsyncClient(
        transport=MockTransport(lambda request: httpx.Response(200))
    )
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
    )
    container: Container = build_container(settings, _TestEnv(), outbound, database.sessions)
    try:
        expected_routes: tuple[tuple[str, str, Permission], ...] = _expected()
        expected_keys: tuple[tuple[str, str], ...] = tuple(
            (method, path) for method, path, _ in expected_routes
        )
        actual: list[tuple[str, str, Permission]] = []
        for router in container.routers:
            for route in router.routes:
                if not isinstance(route, APIRoute):
                    continue
                permissions: tuple[Permission, ...] = _permissions(route.dependant)
                for method in route.methods or ():
                    if (method, route.path) in expected_keys:
                        assert len(permissions) == 1, f"{method} {route.path}: {permissions}"
                        actual.append((method, route.path, permissions[0]))
        for expected in expected_routes:
            matches: tuple[tuple[str, str, Permission], ...] = tuple(
                route for route in actual if route[:2] == expected[:2]
            )
            assert matches == (expected,), (expected, matches)
        assert len(actual) == len(expected_routes), actual
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()
