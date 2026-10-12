"""API regressions for issue-detail routes and permission declarations."""

from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from httpx import Response as HttpResponse
from sqlalchemy import select

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.common.auth import PermissionDependency, Principal
from telescreen.api.routers.issue_activity import build_issue_activity_router
from telescreen.api.routers.issue_events import build_issue_events_router
from telescreen.api.routers.issue_external import (
    IssueExternalRouterDeps,
    build_issue_external_router,
)
from telescreen.api.routers.issues_common import IssuesRouterDeps
from telescreen.api.routers.issues_detail import build_issues_detail_router
from telescreen.core.json import JsonValue, parse_json
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import PrincipalKind, Role, RuleClass
from telescreen.db.models import FindingRow
from telescreen.issues.external.links import ExternalLinkService
from telescreen.ports.findings import MaskedFinding, RecordOutcome
from tests.issues.test_issues import (
    IssueHarness,
    RecordingIndexer,
    make_issue_harness,
    masked_finding,
    resource_scan,
)


@dataclass(frozen=True)
class _PermissionPrincipal:
    """Provide an authorized member principal for route-level regression tests."""

    value: Principal

    async def __call__(self) -> Principal:
        """Return the configured principal."""
        return self.value


@pytest.mark.asyncio
async def test_new_issue_routers_declare_one_permission_per_route() -> None:
    """Every newly built issue-detail route has exactly one permission declaration."""
    harness: IssueHarness = await make_issue_harness()
    try:
        context: ApiContext = ApiContext(harness.db.sessions, harness.clock, harness.audit)
        deps: IssuesRouterDeps = IssuesRouterDeps(
            context,
            harness.service,
            RecordingIndexer([], []),
        )
        external: IssueExternalRouterDeps = IssueExternalRouterDeps(
            context,
            ExternalLinkService(harness.redactor, harness.audit, harness.clock),
            None,
            (),
        )
        routers: tuple[APIRouter, ...] = (
            build_issue_events_router(deps),
            build_issue_activity_router(deps),
            build_issue_external_router(external),
        )
        for router in routers:
            routes: tuple[APIRoute, ...] = tuple(
                route for route in router.routes if isinstance(route, APIRoute)
            )
            assert routes
            for route in routes:
                permissions: tuple[PermissionDependency, ...] = tuple(
                    dependency.call
                    for dependency in route.dependant.dependencies
                    if isinstance(dependency.call, PermissionDependency)
                )
                assert len(permissions) == 1, route.path
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_b1_events_order_ascending_returns_oldest_finding() -> None:
    """B1: the existing events list accepts asc and returns the oldest observation first."""
    harness: IssueHarness = await make_issue_harness()
    try:
        issue_id: str | None = None
        first_finding_id: str | None = None
        for index in range(3):
            finding: MaskedFinding = masked_finding(
                harness, RuleClass.CREDENTIAL, f"api-b1-{index}"
            )
            async with harness.db.sessions.begin() as session:
                outcome: RecordOutcome = await harness.service.record_scan(
                    session,
                    resource_scan(
                        harness,
                        (finding,),
                        f"api-b1-scan-{index}",
                        resource_locator="api-b1-resource",
                    ),
                )
                issue_id = outcome.issue_ids[0]
                if index == 0:
                    first_finding_id = await session.scalar(
                        select(FindingRow.id).where(FindingRow.issue_id == issue_id)
                    )
            harness.clock.advance(timedelta(minutes=1))
        assert issue_id is not None
        context: ApiContext = ApiContext(harness.db.sessions, harness.clock, harness.audit)
        deps: IssuesRouterDeps = IssuesRouterDeps(
            context,
            harness.service,
            RecordingIndexer([], []),
        )
        router: APIRouter = build_issues_detail_router(deps)
        principal: Principal = Principal(
            PrincipalKind.OIDC,
            None,
            "member-viewer",
            Role.ADMIN,
            frozenset(Permission),
        )
        app: FastAPI = FastAPI()
        app.include_router(router)
        install_error_handlers(app)
        provider: _PermissionPrincipal = _PermissionPrincipal(principal)
        for route in router.routes:
            if isinstance(route, APIRoute):
                for dependency in route.dependant.dependencies:
                    if isinstance(dependency.call, PermissionDependency):
                        app.dependency_overrides[dependency.call] = provider
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response: HttpResponse = await client.get(f"/api/issues/{issue_id}/events?order=asc")
        payload: JsonValue = parse_json(response.text)
        items_value: JsonValue | None = payload.get("items")
        assert items_value is not None
        items: tuple[JsonValue, ...] | None = items_value.as_list()
        assert response.status_code == 200
        assert items is not None and items
        finding_id: JsonValue | None = items[0].get("id")
        assert finding_id is not None
        assert finding_id.as_str() == first_finding_id
    finally:
        await harness.close()
