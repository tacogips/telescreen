"""Per-router table test: every route declares exactly one permission or external kind."""

from dataclasses import dataclass

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from telescreen.api.common import ExternalAuthDeclaration, PermissionDependency
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ExternalAuthKind
from tests.api.v12_rbac.rbac_world import RbacWorld, make_world


@dataclass(frozen=True)
class RouteRule:
    """The declared authorization of one route of one router module."""

    module: str
    method: str
    path: str
    permission: Permission | None = None
    external: ExternalAuthKind | None = None


@dataclass(frozen=True)
class Declared:
    """Authorization markers found on one route."""

    module: str
    method: str
    path: str
    permissions: tuple[Permission, ...]
    externals: tuple[ExternalAuthKind, ...]


TABLE: tuple[RouteRule, ...] = (
    RouteRule("health", "GET", "/api/health", external=ExternalAuthKind.PUBLIC),
    RouteRule("issues", "GET", "/api/issues", Permission.ISSUES_READ),
    RouteRule("issues", "GET", "/api/issues/stats", Permission.ISSUES_READ),
    RouteRule("issues", "GET", "/api/issues/{issue_id}", Permission.ISSUES_READ),
    RouteRule("issues", "GET", "/api/issues/{issue_id}/events", Permission.ISSUES_READ),
    RouteRule("issues", "GET", "/api/issues/{issue_id}/history", Permission.ISSUES_READ),
    RouteRule("issues", "GET", "/api/issues/{issue_id}/occurrences", Permission.ISSUES_READ),
    RouteRule("issues", "POST", "/api/issues/{issue_id}/remediation", Permission.ISSUES_WRITE),
    RouteRule("issues", "POST", "/api/issues/{issue_id}/resolve", Permission.ISSUES_WRITE),
    RouteRule("issues", "POST", "/api/issues/{issue_id}/release", Permission.ISSUES_WRITE),
    RouteRule("issues", "PATCH", "/api/issues/{issue_id}", Permission.ISSUES_WRITE),
    RouteRule("issues", "POST", "/api/issues/{issue_id}/comments", Permission.ISSUES_WRITE),
    RouteRule("issues", "POST", "/api/issues/{issue_id}/exceptions", Permission.EXCEPTIONS_WRITE),
    RouteRule("exceptions", "GET", "/api/exceptions", Permission.EXCEPTIONS_READ),
    RouteRule(
        "exceptions", "POST", "/api/exceptions/{exception_id}/approve", Permission.EXCEPTIONS_WRITE
    ),
    RouteRule(
        "exceptions", "POST", "/api/exceptions/{exception_id}/reject", Permission.EXCEPTIONS_WRITE
    ),
    RouteRule(
        "exceptions", "POST", "/api/exceptions/{exception_id}/revoke", Permission.EXCEPTIONS_WRITE
    ),
    RouteRule("workflows", "GET", "/api/workflows", Permission.WORKFLOWS_READ),
    RouteRule("workflows", "GET", "/api/workflows/schema", Permission.WORKFLOWS_READ),
    RouteRule(
        "workflows", "GET", "/api/workflows/{name}/versions/{version}", Permission.WORKFLOWS_READ
    ),
    RouteRule("workflows", "GET", "/api/workflows/{name}", Permission.WORKFLOWS_READ),
    RouteRule("workflows", "POST", "/api/workflows/validate", Permission.WORKFLOWS_WRITE),
    RouteRule("workflows", "POST", "/api/workflows", Permission.WORKFLOWS_WRITE),
    RouteRule("workflows", "POST", "/api/workflows/{name}/activate", Permission.WORKFLOWS_ACTIVATE),
    RouteRule("workflows", "POST", "/api/workflows/{name}/runs", Permission.RUNS_WRITE),
    RouteRule("runs", "GET", "/api/runs", Permission.RUNS_READ),
    RouteRule("runs", "GET", "/api/runs/{run_id}", Permission.RUNS_READ),
    RouteRule("runs", "POST", "/api/runs/{run_id}/cancel", Permission.RUNS_WRITE),
    RouteRule("human_tasks", "GET", "/api/human-tasks", Permission.HUMAN_TASKS_READ),
    RouteRule(
        "human_tasks",
        "POST",
        "/api/human-tasks/{task_id}/complete",
        Permission.HUMAN_TASKS_COMPLETE,
    ),
    RouteRule("evidence", "PUT", "/api/evidence", Permission.EVIDENCE_UPLOAD),
    RouteRule("evidence", "GET", "/api/evidence", Permission.EVIDENCE_READ),
    RouteRule("evidence", "GET", "/api/evidence/{evidence_id}", Permission.EVIDENCE_READ),
    RouteRule("evidence", "GET", "/api/evidence/{evidence_id}/content", Permission.EVIDENCE_READ),
    RouteRule("policies", "GET", "/api/policies", Permission.POLICIES_READ),
    RouteRule("policies", "GET", "/api/policies/{name}", Permission.POLICIES_READ),
    RouteRule(
        "policies", "GET", "/api/policies/{name}/versions/{version}", Permission.POLICIES_READ
    ),
    RouteRule("policies", "POST", "/api/policies/validate", Permission.POLICIES_READ),
    RouteRule("policies", "POST", "/api/policies", Permission.POLICIES_WRITE),
    RouteRule("policies", "POST", "/api/policies/{name}/activate", Permission.POLICIES_WRITE),
    RouteRule("policies", "POST", "/api/policies/{name}/disable", Permission.POLICIES_WRITE),
    RouteRule("escalation", "GET", "/api/escalation-policies", Permission.ESCALATION_READ),
    RouteRule("escalation", "POST", "/api/escalation-policies", Permission.ESCALATION_WRITE),
    RouteRule("escalation", "PUT", "/api/escalation-policies/{name}", Permission.ESCALATION_WRITE),
    RouteRule("directory", "GET", "/api/members", Permission.DIRECTORY_READ),
    RouteRule("directory", "GET", "/api/teams", Permission.DIRECTORY_READ),
    RouteRule("directory", "GET", "/api/members/{handle}/identities", Permission.DIRECTORY_READ),
    RouteRule("directory", "POST", "/api/members", Permission.DIRECTORY_WRITE),
    RouteRule("directory", "POST", "/api/teams", Permission.DIRECTORY_WRITE),
    RouteRule("directory", "PUT", "/api/teams/{slug}/members", Permission.DIRECTORY_WRITE),
    RouteRule("directory", "PUT", "/api/teams/{slug}/lead", Permission.DIRECTORY_WRITE),
    RouteRule("directory", "POST", "/api/members/{handle}/identities", Permission.DIRECTORY_WRITE),
    RouteRule(
        "directory",
        "DELETE",
        "/api/members/{handle}/identities/{identity_id}",
        Permission.DIRECTORY_WRITE,
    ),
    RouteRule("collectors", "GET", "/api/collectors", Permission.COLLECTORS_READ),
    RouteRule(
        "collectors", "GET", "/api/collectors/{config_id}/coverage", Permission.COLLECTORS_READ
    ),
    RouteRule(
        "collectors",
        "GET",
        "/api/collectors/{config_id}/coverage/resources",
        Permission.COLLECTORS_READ,
    ),
    RouteRule("collectors", "POST", "/api/collectors", Permission.COLLECTORS_WRITE),
    RouteRule("collectors", "PATCH", "/api/collectors/{config_id}", Permission.COLLECTORS_WRITE),
    RouteRule("collectors", "POST", "/api/collectors/{config_id}/run", Permission.COLLECTORS_WRITE),
    RouteRule(
        "collectors", "POST", "/api/collectors/{config_id}/sweep", Permission.COLLECTORS_WRITE
    ),
    RouteRule(
        "collectors",
        "POST",
        "/api/collectors/{kind}/webhook",
        external=ExternalAuthKind.PROVIDER_SIGNATURE,
    ),
    RouteRule("map_jobs", "GET", "/api/map-jobs", Permission.MAP_JOBS_READ),
    RouteRule("map_jobs", "GET", "/api/map-jobs/{job_id}", Permission.MAP_JOBS_READ),
    RouteRule("map_jobs", "POST", "/api/map-jobs/{job_id}/cancel", Permission.MAP_JOBS_WRITE),
    RouteRule("map_jobs", "POST", "/api/map-jobs/{job_id}/retry-failed", Permission.MAP_JOBS_WRITE),
    RouteRule(
        "police", "GET", "/api/police/v1/openapi.json", external=ExternalAuthKind.POLICE_CONTRACT
    ),
    RouteRule(
        "police",
        "POST",
        "/api/police/v1/workers/register",
        external=ExternalAuthKind.POLICE_CONTRACT,
    ),
    RouteRule(
        "police",
        "POST",
        "/api/police/v1/workers/{worker_id}/claim",
        external=ExternalAuthKind.POLICE_CONTRACT,
    ),
    RouteRule(
        "police",
        "POST",
        "/api/police/v1/workers/{worker_id}/heartbeat",
        external=ExternalAuthKind.POLICE_CONTRACT,
    ),
    RouteRule(
        "police",
        "POST",
        "/api/police/v1/tasks/{task_id}/result",
        external=ExternalAuthKind.POLICE_CONTRACT,
    ),
    RouteRule(
        "police",
        "POST",
        "/api/police/v1/callbacks/{task_id}",
        external=ExternalAuthKind.POLICE_CONTRACT,
    ),
    RouteRule("police", "GET", "/api/police/workers", Permission.POLICE_READ),
    RouteRule("police", "POST", "/api/police/workers/{worker_id}/disable", Permission.POLICE_WRITE),
    RouteRule("audit", "GET", "/api/audit/search", Permission.AUDIT_READ),
    RouteRule("audit", "GET", "/api/audit/verify", Permission.AUDIT_READ),
    RouteRule("search", "GET", "/api/search", Permission.SEARCH_READ),
    RouteRule("chat", "POST", "/api/chat", Permission.CHAT_USE),
    RouteRule("ingest", "POST", "/api/ingest/v1/scan", Permission.INGEST_PUSH),
    RouteRule("ingest", "POST", "/api/ingest/v1/findings", Permission.INGEST_PUSH),
    RouteRule("ingest", "GET", "/api/ingest/v1/sources/{name}/config", Permission.INGEST_PUSH),
)
MODULES: tuple[str, ...] = (
    "audit",
    "chat",
    "collectors",
    "directory",
    "escalation",
    "evidence",
    "exceptions",
    "health",
    "human_tasks",
    "ingest",
    "issues",
    "map_jobs",
    "police",
    "policies",
    "runs",
    "search",
    "workflows",
)
ROUTER_PACKAGE: str = "telescreen.api.routers."


def _markers(dependant: Dependant) -> tuple[tuple[Permission, ...], tuple[ExternalAuthKind, ...]]:
    """Collect permission and external markers from a route's dependency tree."""
    permissions: list[Permission] = []
    externals: list[ExternalAuthKind] = []
    for child in dependant.dependencies:
        if isinstance(child.call, PermissionDependency):
            permissions.append(child.call.permission)
        elif isinstance(child.call, ExternalAuthDeclaration):
            externals.append(child.call.kind)
        nested: tuple[tuple[Permission, ...], tuple[ExternalAuthKind, ...]] = _markers(child)
        permissions.extend(nested[0])
        externals.extend(nested[1])
    return tuple(permissions), tuple(externals)


def _declared_routes(world: RbacWorld) -> tuple[Declared, ...]:
    """Return the markers of every route defined by the 17 existing router modules."""
    declared: list[Declared] = []
    for router in world.container.routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            module_path: str = route.endpoint.__module__
            if not module_path.startswith(ROUTER_PACKAGE):
                continue
            module: str = module_path.removeprefix(ROUTER_PACKAGE)
            if module not in MODULES:
                continue
            markers: tuple[tuple[Permission, ...], tuple[ExternalAuthKind, ...]] = _markers(
                route.dependant
            )
            for method in sorted(route.methods or ()):
                declared.append(Declared(module, method, route.path, markers[0], markers[1]))
    return tuple(declared)


@pytest.mark.asyncio
async def test_every_existing_router_route_declares_its_table_authorization() -> None:
    """Each route has exactly one PermissionDependency or one ExternalAuthDeclaration."""
    world: RbacWorld = await make_world()
    try:
        declared: tuple[Declared, ...] = _declared_routes(world)
    finally:
        await world.close()
    assert {item.module for item in declared} == set(MODULES)
    expected: set[tuple[str, str, str]] = {(r.module, r.method, r.path) for r in TABLE}
    actual: set[tuple[str, str, str]] = {(d.module, d.method, d.path) for d in declared}
    assert actual == expected
    for item in declared:
        rule: RouteRule = next(
            r
            for r in TABLE
            if (r.module, r.method, r.path) == (item.module, item.method, item.path)
        )
        assert len(item.permissions) + len(item.externals) == 1, item
        if rule.permission is not None:
            assert item.permissions == (rule.permission,), item
        else:
            assert item.externals == (rule.external,), item
