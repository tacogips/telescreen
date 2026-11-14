"""Settings routes use Notification storage and replica contact DTOs only."""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common.context import ApiContext
from telescreen.api.common.errors import install_error_handlers
from telescreen.api.routers.notification_settings import (
    NotificationSettingsRouterDeps,
    build_notification_settings_router,
)
from telescreen.core.clock import FakeClock
from telescreen.core.permissions import role_permissions
from telescreen.core.vocabulary import NotificationCategory, PrincipalKind, Role
from telescreen.db.ownership import Store
from telescreen.notifications.preferences import (
    MemberContact,
    PreferenceMembers,
    PreferenceRepository,
    PreferenceService,
)
from telescreen.ports.principal import Principal
from tests.notifications.v13.fakes import RecordingAudit
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class StaticContacts(PreferenceMembers):
    """Return one replica-like member contact without a members table."""

    contact: MemberContact | None

    async def member(self, session: AsyncSession, member_id: str) -> MemberContact | None:
        """Return the configured identity for the authenticated member only."""
        del session
        return self.contact if self.contact is not None and self.contact.id == member_id else None


def _app(harness: SqliteHarness, contacts: StaticContacts) -> FastAPI:
    """Create the existing settings router with Notification-only dependencies."""
    clock: FakeClock = FakeClock(datetime(2026, 10, 16, tzinfo=UTC))
    principal: Principal = Principal(
        PrincipalKind.OIDC,
        None,
        "member-1",
        Role.ADMIN,
        role_permissions(Role.ADMIN),
    )

    async def authenticate(request: Request) -> Principal:
        del request
        return principal

    ctx: ApiContext = ApiContext(
        harness.sessions,
        clock,
        RecordingAudit(),
        authentication=authenticate,
    )
    preferences: PreferenceService = PreferenceService(PreferenceRepository(), clock, contacts)
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(
        build_notification_settings_router(NotificationSettingsRouterDeps(ctx, preferences))
    )
    return app


async def test_settings_get_and_put_work_without_members_table() -> None:
    """Persist preferences using DTO contacts in the Notification schema."""
    harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    contact: MemberContact = MemberContact("member-1", True, False)
    transport: httpx.ASGITransport = httpx.ASGITransport(app=_app(harness, StaticContacts(contact)))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://notification.test"
    ) as client:
        before: httpx.Response = await client.get("/api/auth/me/notification-settings")
        assert before.status_code == 200
        update: httpx.Response = await client.put(
            "/api/auth/me/notification-settings",
            json={"items": [{"category": NotificationCategory.ALERTS.value, "stored": "email"}]},
        )
        assert update.status_code == 200
        assert '"stored":"email"' in update.text
    await harness.close()


async def test_settings_reject_missing_replica_member_with_existing_error() -> None:
    """Missing replica identity keeps the established forbidden response."""
    harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    transport: httpx.ASGITransport = httpx.ASGITransport(app=_app(harness, StaticContacts(None)))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://notification.test"
    ) as client:
        response: httpx.Response = await client.get("/api/auth/me/notification-settings")
    assert response.status_code == 403
    assert response.json()["error"]["detail"] == "member principal is unavailable"
    await harness.close()
