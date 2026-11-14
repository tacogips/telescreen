"""Notification routes and production replica adapters on a Notification-only store."""

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select

from telescreen.api.common.context import ApiContext
from telescreen.core.clock import FakeClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import TelescreenError
from telescreen.core.permissions import role_permissions
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import (
    DlpPolicy,
    ErrorCode,
    NotificationCategory,
    NotificationChannel,
    NotificationStatus,
    PrincipalKind,
    Role,
)
from telescreen.db.models import NotificationDeliveryStateRow, NotificationRow
from telescreen.db.ownership import Store
from telescreen.notification_service.app import (
    NOTIFICATION_PREFIX,
    SERVICE_HEADER,
    NotificationRuntime,
    create_notification_app,
)
from telescreen.notification_service.inbox import Producer
from telescreen.notification_service.wiring import build_notification_runtime
from telescreen.notifications.member_send import MemberReferences
from telescreen.ports.directory_projection import (
    DirectoryEntity,
    DirectoryEntityKind,
    MemberEntity,
)
from telescreen.ports.notification_commands import (
    NotificationCommand,
    NotificationCommandReceipt,
    ProducerRedactionHits,
    SendOperation,
)
from telescreen.ports.principal import Principal
from tests.support.db import SqliteHarness, make_sqlite_harness


class _Environment(EnvSource):
    """Resolve isolated service credential references for runtime tests."""

    def get(self, key: str, /) -> str | None:
        """Return distinct local test credentials without using process secrets."""
        return {"TS": "telescreen-test-token", "INV": "inventory-test-token"}.get(key)


async def _build_runtime(
    harness: SqliteHarness,
) -> tuple[NotificationRuntime, httpx.AsyncClient, FakeClock]:
    """Build the application runtime over the supplied Notification-store sessions."""
    clock: FakeClock = FakeClock(datetime(2026, 10, 16, tzinfo=UTC))
    service_http: httpx.AsyncClient = httpx.AsyncClient()
    runtime: NotificationRuntime = build_notification_runtime(
        TelescreenSettings(
            notification_database_url="sqlite+aiosqlite:///:memory:",
            notification_service_token_ref="env:TS",
            notification_inventory_token_ref="env:INV",
            dlp_policy=DlpPolicy.MASK,
        ),
        _Environment(),
        service_http,
        harness.sessions,
        clock,
    )
    return runtime, service_http, clock


def _member_entity(active: bool, revision: int) -> DirectoryEntity:
    """Return the replica member used by settings and notification adapter tests."""
    member: MemberEntity = MemberEntity(
        "member-1", "alice", "Alice", active, "admin", "alice@example.test", None
    )
    return DirectoryEntity(DirectoryEntityKind.MEMBER, member.id, revision, True, member)


def _send_command(clock: FakeClock) -> NotificationCommand:
    """Return one Telescreen inline send resolvable through the replica."""
    operation: SendOperation = SendOperation(
        NotificationChannel.EMAIL,
        ("member:alice",),
        "Safe subject",
        "Safe body",
        "system:test",
        None,
        None,
        None,
        None,
        None,
        False,
        None,
        None,
        True,
        None,
        False,
        ProducerRedactionHits(0, 0),
    )
    return NotificationCommand("event-queued-through-runtime", clock.now(), (operation,))


async def test_runtime_routes_use_replica_preferences_and_fail_closed_test_send() -> None:
    """Exercise app settings, channel flags, unconfigured test sends and replica members."""
    harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    runtime: NotificationRuntime
    service_http: httpx.AsyncClient
    clock: FakeClock
    runtime, service_http, clock = await _build_runtime(harness)
    async with harness.sessions.begin() as session:
        await runtime.replica.apply(session, _member_entity(True, 1))

    async def authenticate(request: Request) -> Principal:
        """Authenticate the request as the seeded replica member for public API tests."""
        del request
        return Principal(
            PrincipalKind.OIDC,
            None,
            "member-1",
            Role.ADMIN,
            role_permissions(Role.ADMIN),
        )

    context: ApiContext = ApiContext(
        harness.sessions,
        clock,
        runtime.context.audit,
        authentication=authenticate,
    )
    runtime = replace(runtime, context=context)
    app: FastAPI = create_notification_app(runtime)
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://notification.test"
        ) as client:
            settings: httpx.Response = await client.get("/api/auth/me/notification-settings")
            assert settings.status_code == 200
            assert settings.json()["items"][0]["available"] == {
                "email": True,
                "slack": False,
            }
            assert settings.json()["items"][0]["effective"] == "email"
            updated: httpx.Response = await client.put(
                "/api/auth/me/notification-settings",
                json={
                    "items": [{"category": NotificationCategory.ALERTS.value, "stored": "email"}]
                },
            )
            assert updated.status_code == 200

            inactive_member: DirectoryEntity = _member_entity(False, 2)
            async with harness.sessions.begin() as session:
                await runtime.replica.apply(session, inactive_member)
            absent: httpx.Response = await client.get("/api/auth/me/notification-settings")
            assert absent.status_code == 403
            assert absent.json()["error"]["detail"] == "member principal is unavailable"

            channels: httpx.Response = await client.get(
                NOTIFICATION_PREFIX + "/channels",
                headers={SERVICE_HEADER: "telescreen-test-token"},
            )
            assert channels.status_code == 200
            assert channels.json() == {"email": False, "slack": False}
            test_result: httpx.Response = await client.post(
                NOTIFICATION_PREFIX + "/test",
                headers={SERVICE_HEADER: "telescreen-test-token"},
                json={"channel": "email", "member_id": "member-1"},
            )
            assert test_result.status_code == 200
            assert test_result.json() == {
                "ok": False,
                "error_code": "integration_unconfigured",
                "notification_id": None,
            }

        reference_adapter: MemberReferences | None = runtime.inbox.member_references
        assert reference_adapter is not None
        async with harness.sessions() as session:
            with pytest.raises(TelescreenError) as raised:
                await reference_adapter.member_reference(session, "member-1")
            assert raised.value.code is ErrorCode.RECIPIENT_UNKNOWN
        async with harness.sessions.begin() as session:
            await runtime.replica.apply(session, _member_entity(True, 3))
        async with harness.sessions() as session:
            assert await reference_adapter.member_reference(session, "member-1") == "member:alice"
    finally:
        await service_http.aclose()
        await harness.close()


async def test_runtime_inbox_uses_notification_task_queue() -> None:
    """An inbox-applied send uses the production queue adapter and stores its schedule."""
    harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    runtime: NotificationRuntime
    service_http: httpx.AsyncClient
    clock: FakeClock
    runtime, service_http, clock = await _build_runtime(harness)
    try:
        async with harness.sessions.begin() as session:
            await runtime.replica.apply(session, _member_entity(True, 1))
        result: NotificationCommandReceipt = await runtime.inbox.receive(
            _send_command(clock), Producer.TELESCREEN
        )
        assert result is NotificationCommandReceipt.APPLIED
        async with harness.sessions() as session:
            notification: NotificationRow | None = await session.scalar(select(NotificationRow))
            assert notification is not None
            state: NotificationDeliveryStateRow | None = await session.get(
                NotificationDeliveryStateRow, notification.id
            )
        assert notification.status == NotificationStatus.QUEUED.value
        assert state is not None
        assert state.attempt == 0
    finally:
        await service_http.aclose()
        await harness.close()
