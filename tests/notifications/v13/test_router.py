"""HTTP tests for member-owned notification settings routes."""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from telescreen.api.common import ApiContext, PermissionDependency, install_error_handlers
from telescreen.api.common.auth import hash_token
from telescreen.api.routers.notification_settings import (
    NotificationSettingsRouterDeps,
    build_notification_settings_router,
)
from telescreen.core.clock import FakeClock
from telescreen.core.permissions import Permission
from telescreen.db.models import ApiTokenRow
from telescreen.notifications.preferences import PreferenceRepository, PreferenceService
from tests.notifications.v13.fakes import RecordingAudit, member_row
from tests.support.db import SqliteHarness, make_sqlite_harness


@pytest.mark.parametrize("bind_member", (True, False))
async def test_settings_routes_round_trip_and_require_member_principal(bind_member: bool) -> None:
    """Protect both routes and reject an otherwise-readable unbound token."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    ctx: ApiContext = ApiContext(harness.sessions, clock, RecordingAudit())
    preferences: PreferenceService = PreferenceService(PreferenceRepository(), clock)
    router: APIRouter = build_notification_settings_router(
        NotificationSettingsRouterDeps(ctx, preferences)
    )
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    async with harness.sessions.begin() as session:
        session.add(member_row("member-1", "alice", "alice@example.test", None))
        session.add(
            ApiTokenRow(
                id="token-1",
                label="settings test",
                token_hash=hash_token("bound-token"),
                scopes="read",
                member_id="member-1" if bind_member else None,
                created_at=clock.now(),
                revoked_at=None,
            )
        )
    route_permissions: list[Permission] = []
    for route in router.routes:
        if isinstance(route, APIRoute):
            permission_dependencies: list[PermissionDependency] = [
                dependency.call
                for dependency in route.dependant.dependencies
                if isinstance(dependency.call, PermissionDependency)
            ]
            assert len(permission_dependencies) == 1
            route_permissions.append(permission_dependencies[0].permission)
    assert route_permissions == [Permission.PROFILE_SELF, Permission.PROFILE_SELF]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://telescreen.test"
    ) as client:
        headers: tuple[tuple[str, str], ...] = (("Authorization", "Bearer bound-token"),)
        if not bind_member:
            response: httpx.Response = await client.get(
                "/api/auth/me/notification-settings", headers=headers
            )
            assert response.status_code == 403
        else:
            response = await client.get("/api/auth/me/notification-settings", headers=headers)
            assert response.status_code == 200
            assert len(response.json()["items"]) == 6
            assert response.json()["items"][0]["available"] == {
                "email": True,
                "slack": False,
            }
            response = await client.put(
                "/api/auth/me/notification-settings",
                headers=headers,
                json={"items": [{"category": "mentions", "stored": "off"}]},
            )
            assert response.status_code == 200
            assert (
                next(item for item in response.json()["items"] if item["category"] == "mentions")[
                    "stored"
                ]
                == "off"
            )
            assert (
                next(item for item in response.json()["items"] if item["category"] == "mentions")[
                    "effective"
                ]
                == "off"
            )
            response = await client.put(
                "/api/auth/me/notification-settings",
                headers=headers,
                json={"items": [{"category": "mentions", "stored": "slack"}]},
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "recipient_unreachable"
    await harness.close()
