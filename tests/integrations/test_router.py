"""Standalone integrations router permission and HTTP contracts."""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from telescreen.api.common import ApiContext, PermissionDependency, install_error_handlers
from telescreen.api.common.auth import hash_token
from telescreen.api.routers.integrations import IntegrationsRouterDeps, build_integrations_router
from telescreen.core.clock import FakeClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.permissions import Permission
from telescreen.db.models import ApiTokenRow
from telescreen.integrations.repository import IntegrationCheckRepository
from telescreen.integrations.status import IntegrationStatusService
from telescreen.integrations.tests import IntegrationTester
from tests.integrations.test_integrations import RecordingAuditLog, StubChannelTester, StubEnv
from tests.support.db import SqliteHarness, make_sqlite_harness


@pytest.mark.asyncio
async def test_built_routes_have_one_permission_each() -> None:
    """Each direct router route declares exactly its contracted permission."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    audit: RecordingAuditLog = RecordingAuditLog()
    settings: TelescreenSettings = TelescreenSettings()
    checks: IntegrationCheckRepository = IntegrationCheckRepository()
    tester: IntegrationTester = IntegrationTester(
        StubChannelTester(), None, None, settings, StubEnv(()), checks, audit, clock
    )
    router: APIRouter = build_integrations_router(
        IntegrationsRouterDeps(
            ApiContext(harness.sessions, clock, audit),
            IntegrationStatusService(settings, checks),
            tester,
            checks,
        )
    )
    expected: tuple[Permission, ...] = (
        Permission.INTEGRATIONS_READ,
        Permission.INTEGRATIONS_TEST,
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
    assert tuple(actual) == expected
    await harness.close()


@pytest.mark.asyncio
async def test_viewer_get_is_forbidden_and_unconfigured_test_is_503() -> None:
    """A read token cannot view integrations and admin receives safe 503."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    audit: RecordingAuditLog = RecordingAuditLog()
    settings: TelescreenSettings = TelescreenSettings()
    checks: IntegrationCheckRepository = IntegrationCheckRepository()
    tester: IntegrationTester = IntegrationTester(
        StubChannelTester(), None, None, settings, StubEnv(()), checks, audit, clock
    )
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(
        build_integrations_router(
            IntegrationsRouterDeps(
                ApiContext(harness.sessions, clock, audit),
                IntegrationStatusService(settings, checks),
                tester,
                checks,
            )
        )
    )
    async with harness.sessions.begin() as session:
        session.add_all(
            (
                ApiTokenRow(
                    id="viewer",
                    label="viewer",
                    token_hash=hash_token("viewer-token"),
                    scopes="read",
                    member_id=None,
                    created_at=clock.now(),
                    revoked_at=None,
                ),
                ApiTokenRow(
                    id="admin",
                    label="admin",
                    token_hash=hash_token("admin-token"),
                    scopes="admin",
                    member_id=None,
                    created_at=clock.now(),
                    revoked_at=None,
                ),
            )
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        viewer: httpx.Response = await client.get(
            "/api/integrations", headers={"Authorization": "Bearer viewer-token"}
        )
        unavailable: httpx.Response = await client.post(
            "/api/integrations/github_issues/test",
            headers={"Authorization": "Bearer admin-token"},
        )
    assert viewer.status_code == 403
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "error": {"code": "integration_unconfigured", "detail": "integration is not configured"}
    }
    await harness.close()


@pytest.mark.asyncio
async def test_status_http_response_omits_configured_tokens_hosts_and_addresses() -> None:
    """Authenticated status JSON omits every configured secret and connection value."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    audit: RecordingAuditLog = RecordingAuditLog()
    checks: IntegrationCheckRepository = IntegrationCheckRepository()
    settings: TelescreenSettings = TelescreenSettings(
        slack_bot_token_ref="env:BOT_TOKEN_REFERENCE_PRIVATE",
        smtp_host="mail-host-private.invalid",
        smtp_from="private-sender@example.invalid",
        github_issues_token_ref="env:GITHUB_TOKEN_REFERENCE_PRIVATE",
        github_issues_repos=("owner/repo",),
    )
    tester: IntegrationTester = IntegrationTester(
        StubChannelTester(),
        None,
        None,
        settings,
        StubEnv(
            (
                ("BOT_TOKEN_REFERENCE_PRIVATE", "private-bot-token-value"),
                ("GITHUB_TOKEN_REFERENCE_PRIVATE", "private-github-token-value"),
            )
        ),
        checks,
        audit,
        clock,
    )
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(
        build_integrations_router(
            IntegrationsRouterDeps(
                ApiContext(harness.sessions, clock, audit),
                IntegrationStatusService(settings, checks),
                tester,
                checks,
            )
        )
    )
    async with harness.sessions.begin() as session:
        session.add(
            ApiTokenRow(
                id="admin",
                label="admin",
                token_hash=hash_token("admin-token"),
                scopes="admin",
                member_id=None,
                created_at=clock.now(),
                revoked_at=None,
            )
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response: httpx.Response = await client.get(
            "/api/integrations", headers={"Authorization": "Bearer admin-token"}
        )
    assert response.status_code == 200
    for private_value in (
        "BOT_TOKEN_REFERENCE_PRIVATE",
        "private-bot-token-value",
        "mail-host-private.invalid",
        "private-sender@example.invalid",
        "GITHUB_TOKEN_REFERENCE_PRIVATE",
        "private-github-token-value",
    ):
        assert private_value not in response.text
    await harness.close()
