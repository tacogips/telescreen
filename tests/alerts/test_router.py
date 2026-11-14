"""Alert API permission and secret-masking tests."""

from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import select

from telescreen.alerts.repository import AlertRepository
from telescreen.alerts.service import AlertRuleService
from telescreen.api.common import ApiContext, PermissionDependency, install_error_handlers
from telescreen.api.common.auth import hash_token
from telescreen.api.routers.alerts import AlertsRouterDeps, build_alerts_router
from telescreen.core.clock import FakeClock
from telescreen.core.permissions import Permission
from telescreen.db.models import AlertRuleRow, ApiTokenRow
from telescreen.masking.redact import Redactor
from tests.notifications.v13.fakes import RecordingAudit
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, github_token


def _service(clock: FakeClock) -> AlertRuleService:
    """Build an alert service with only local test dependencies."""
    return AlertRuleService(
        Redactor.default(), RecordingAudit(), clock, None, None, AlertRepository()
    )


async def test_every_alert_route_has_exactly_one_expected_permission() -> None:
    """The standalone built router declares one permission for every route."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    router: APIRouter = build_alerts_router(
        AlertsRouterDeps(ApiContext(harness.sessions, clock, RecordingAudit()), _service(clock))
    )
    actual: list[Permission] = []
    for route in router.routes:
        if isinstance(route, APIRoute):
            dependencies: list[PermissionDependency] = [
                dependency.call
                for dependency in route.dependant.dependencies
                if isinstance(dependency.call, PermissionDependency)
            ]
            assert len(dependencies) == 1
            actual.append(dependencies[0].permission)
    assert actual == [
        Permission.ALERTS_READ,
        Permission.ALERTS_WRITE,
        Permission.ALERTS_WRITE,
        Permission.ALERTS_READ,
        Permission.ALERTS_WRITE,
        Permission.ALERTS_WRITE,
        Permission.ALERTS_WRITE,
        Permission.ALERTS_WRITE,
        Permission.ALERTS_MUTE,
        Permission.ALERTS_MUTE,
        Permission.ALERTS_READ,
    ]
    await harness.close()


async def test_secret_rule_name_returns_422_without_row_or_echo() -> None:
    """A rule name containing a sample secret returns path/code only and persists nothing."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    ctx: ApiContext = ApiContext(harness.sessions, clock, RecordingAudit())
    router: APIRouter = build_alerts_router(AlertsRouterDeps(ctx, _service(clock)))
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    sample: Sample = github_token()
    async with harness.sessions.begin() as session:
        session.add(
            ApiTokenRow(
                id="admin-token",
                label="test",
                token_hash=hash_token("admin-secret-token"),
                scopes="admin",
                member_id=None,
                created_at=clock.now(),
                revoked_at=None,
            )
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response: httpx.Response = await client.post(
            "/api/alert-rules",
            headers={"Authorization": "Bearer admin-secret-token"},
            json={
                "name": sample.value,
                "trigger": {"kind": "issue_created"},
                "filters": {},
                "actions": {
                    "targets": [
                        {
                            "recipients": ["email:alerts@example.invalid"],
                            "channels": ["email"],
                        }
                    ]
                },
            },
        )
    assert response.status_code == 422
    assert sample.value not in response.text
    assert response.json() == {
        "valid": False,
        "errors": [{"field": "/name", "code": "raw_value_detected"}],
    }
    async with harness.sessions() as session:
        rows: tuple[AlertRuleRow, ...] = tuple((await session.scalars(select(AlertRuleRow))).all())
    assert rows == ()
    await harness.close()
