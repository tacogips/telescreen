"""Full-app authorization declaration and v1 scope compatibility checks."""

from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import MockTransport
from starlette.routing import Route

from telescreen.api.app import create_app
from telescreen.api.common import ExternalAuthDeclaration, PermissionDependency
from telescreen.core.config import TelescreenSettings
from telescreen.core.permissions import Permission, scope_permissions
from telescreen.core.vocabulary import ApiScope, ExternalAuthKind
from telescreen.wiring import Container, build_container, close_container
from tests.api.v12_rbac.test_v12_rbac_router_permissions import TABLE as LEGACY_ROUTES
from tests.identity.oidc_fixtures import ISSUER, base_settings
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.oidc import TestIdp as LocalIdp


@dataclass(frozen=True)
class _TestEnv:
    """Provide the fingerprint secret required by the container."""

    def get(self, key: str, /) -> str | None:
        """Return the local test fingerprint secret by reference name."""
        return "w" * 32 if key == "TELESCREEN_TEST_KEY" else None


def _markers(dependant: Dependant) -> tuple[tuple[Permission, ...], tuple[ExternalAuthKind, ...]]:
    """Collect authorization markers recursively from a route's dependencies."""
    permissions: list[Permission] = []
    externals: list[ExternalAuthKind] = []
    for dependency in dependant.dependencies:
        if isinstance(dependency.call, PermissionDependency):
            permissions.append(dependency.call.permission)
        elif isinstance(dependency.call, ExternalAuthDeclaration):
            externals.append(dependency.call.kind)
        nested: tuple[tuple[Permission, ...], tuple[ExternalAuthKind, ...]] = _markers(dependency)
        permissions.extend(nested[0])
        externals.extend(nested[1])
    return tuple(permissions), tuple(externals)


def _accepted_scopes(permission: Permission) -> frozenset[ApiScope]:
    """Return legacy token scopes that grant the specified route capability."""
    return frozenset(
        scope for scope in ApiScope if permission in scope_permissions(frozenset((scope,)))
    )


V1_SCOPE_TABLE: tuple[tuple[Permission, frozenset[ApiScope]], ...] = (
    (Permission.ISSUES_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.EXCEPTIONS_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.WORKFLOWS_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.RUNS_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.HUMAN_TASKS_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.EVIDENCE_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.POLICIES_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.ESCALATION_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.DIRECTORY_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.COLLECTORS_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.MAP_JOBS_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.POLICE_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.AUDIT_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.SEARCH_READ, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.CHAT_USE, frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.ISSUES_WRITE, frozenset((ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.EXCEPTIONS_WRITE, frozenset((ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.WORKFLOWS_WRITE, frozenset((ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.RUNS_WRITE, frozenset((ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.HUMAN_TASKS_COMPLETE, frozenset((ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.EVIDENCE_UPLOAD, frozenset((ApiScope.WRITE, ApiScope.ADMIN))),
    (Permission.WORKFLOWS_ACTIVATE, frozenset((ApiScope.ADMIN,))),
    (Permission.POLICIES_WRITE, frozenset((ApiScope.ADMIN,))),
    (Permission.ESCALATION_WRITE, frozenset((ApiScope.ADMIN,))),
    (Permission.DIRECTORY_WRITE, frozenset((ApiScope.ADMIN,))),
    (Permission.COLLECTORS_WRITE, frozenset((ApiScope.ADMIN,))),
    (Permission.MAP_JOBS_WRITE, frozenset((ApiScope.ADMIN,))),
    (Permission.POLICE_WRITE, frozenset((ApiScope.ADMIN,))),
    (Permission.INGEST_PUSH, frozenset((ApiScope.INGEST, ApiScope.ADMIN))),
)


@pytest.mark.asyncio
async def test_full_app_routes_declare_auth_and_preserve_v1_scope_behavior() -> None:
    """Require one auth declaration per API route and retain all v1 scope grants."""
    database: SqliteHarness = await make_sqlite_harness()
    idp: LocalIdp = LocalIdp.generate(ISSUER, "telescreen-ui")
    outbound: httpx.AsyncClient = httpx.AsyncClient(transport=MockTransport(idp.mock_handler))
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
        oidc=base_settings(),
    )
    container: Container = build_container(settings, _TestEnv(), outbound, database.sessions)
    try:
        app: FastAPI = create_app(container)
        external: set[tuple[str, str, str]] = set()
        permissions: list[tuple[tuple[str, str], Permission]] = []
        route_errors: list[str] = []
        included_router_count: int = sum(
            1 for route in app.routes if not isinstance(route, (APIRoute, Route))
        )
        assert included_router_count == len(container.routers)
        for router in container.routers:
            for route in router.routes:
                if not isinstance(route, APIRoute):
                    continue
                markers: tuple[tuple[Permission, ...], tuple[ExternalAuthKind, ...]] = _markers(
                    route.dependant
                )
                if len(markers[0]) + len(markers[1]) != 1:
                    route_errors.append(f"{','.join(sorted(route.methods or ()))} {route.path}")
                    continue
                for method in route.methods or ():
                    key: tuple[str, str] = (method, route.path)
                    if markers[0]:
                        permissions.append((key, markers[0][0]))
                    else:
                        external.add((method, route.path, markers[1][0].value))
        assert not route_errors, route_errors
        expected_external: set[tuple[str, str, str]] = {
            ("GET", "/api/health", ExternalAuthKind.PUBLIC.value),
            ("GET", "/api/auth/config", ExternalAuthKind.PUBLIC.value),
            ("POST", "/api/auth/session", ExternalAuthKind.OIDC_SIGN_IN.value),
            (
                "POST",
                "/api/collectors/{kind}/webhook",
                ExternalAuthKind.PROVIDER_SIGNATURE.value,
            ),
            (
                "GET",
                "/api/police/v1/openapi.json",
                ExternalAuthKind.POLICE_CONTRACT.value,
            ),
            (
                "POST",
                "/api/police/v1/workers/register",
                ExternalAuthKind.POLICE_CONTRACT.value,
            ),
            (
                "POST",
                "/api/police/v1/workers/{worker_id}/claim",
                ExternalAuthKind.POLICE_CONTRACT.value,
            ),
            (
                "POST",
                "/api/police/v1/workers/{worker_id}/heartbeat",
                ExternalAuthKind.POLICE_CONTRACT.value,
            ),
            (
                "POST",
                "/api/police/v1/tasks/{task_id}/result",
                ExternalAuthKind.POLICE_CONTRACT.value,
            ),
            (
                "POST",
                "/api/police/v1/callbacks/{task_id}",
                ExternalAuthKind.POLICE_CONTRACT.value,
            ),
        }
        assert external == expected_external
        for legacy in LEGACY_ROUTES:
            if legacy.permission is None:
                continue
            route_key: tuple[str, str] = (legacy.method, legacy.path)
            declared_permission: Permission | None = next(
                (permission for key, permission in permissions if key == route_key), None
            )
            assert declared_permission is not None, f"missing {legacy.method} {legacy.path}"
            assert declared_permission is legacy.permission, route_key
            accepted: frozenset[ApiScope] = _accepted_scopes(declared_permission)
            expected_entry: tuple[Permission, frozenset[ApiScope]] | None = next(
                (entry for entry in V1_SCOPE_TABLE if entry[0] is declared_permission), None
            )
            assert expected_entry is not None, (
                f"missing expected scope table entry for {legacy.method} {legacy.path}"
            )
            expected: frozenset[ApiScope] = expected_entry[1]
            assert accepted == expected, (
                f"{legacy.method} {legacy.path}: actual scopes {accepted}, expected {expected}"
            )
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()
