"""Split topology composition and proxy boundary regressions."""

import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, FakeClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource, SecretValue
from telescreen.core.vocabulary import EngineTaskKind, NotificationChannel
from telescreen.db.models import EngineTaskRow, MemberRow, ProjectionChangeRow
from telescreen.db.models.notification_handoff import NotificationOutboxRow
from telescreen.integrations.notification_delivery import (
    NotificationCommandDeliveryHandler,
    NotificationDeliveryNoop,
)
from telescreen.inventory_service.bureau import build_proxy_router
from telescreen.inventory_service.transport import ServiceHttp
from telescreen.ports.notifications import NotificationRequest
from telescreen.ports.workflow import TaskHandler
from telescreen.projections.markers import uninstall_projection_markers
from telescreen.projections.relay import ProjectionRelayHandler
from telescreen.wiring import Container, build_container, close_container
from telescreen.wiring_builders import RuntimePrimitives
from tests.support.db import SqliteHarness, make_sqlite_harness

_NOW: datetime = datetime(2026, 10, 16, tzinfo=UTC)


@dataclass
class _SplitEnv(EnvSource):
    """Resolve only test service secrets and record configuration reads."""

    values: tuple[tuple[str, str], ...]
    reads: list[str] = field(default_factory=list)

    def get(self, key: str, /) -> str | None:
        """Return one configured secret, rejecting local delivery credentials."""
        self.reads.append(key)
        if key in ("SLACK_SECRET", "SMTP_SECRET"):
            raise AssertionError("split mode resolved a local delivery secret")
        return next((value for name, value in self.values if name == key), None)


def _unexpected_build_primitives(
    settings: TelescreenSettings,
    env: EnvSource,
    http: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession] | None,
    clock: Clock | None,
    inventory_sessions: async_sessionmaker[AsyncSession] | None,
) -> RuntimePrimitives:
    """Fail if topology validation allows any resource construction to begin."""
    del settings, env, http, sessions, clock, inventory_sessions
    raise AssertionError("resource construction began before topology validation")


def test_split_container_import_gate_and_no_delivery_secrets() -> None:
    """Build split composition in isolation without loading local notification code."""
    program: str = """
import asyncio
import sys
import httpx
from telescreen.core.config import TelescreenSettings
from telescreen.wiring import build_container, close_container

class Env:
    def get(self, key, /):
        if key in ('SLACK_SECRET', 'SMTP_SECRET'):
            raise AssertionError('split mode requested a local delivery secret')
        values = {
            'FINGERPRINT_SECRET': 'f' * 32,
            'INVENTORY_TOKEN': 'i' * 32,
            'NOTIFICATION_TOKEN': 'n' * 32,
        }
        return values.get(key)

def reply(request):
    return httpx.Response(404)

settings = TelescreenSettings(
    database_url='sqlite+aiosqlite:///:memory:',
    fingerprint_key_ref='env:FINGERPRINT_SECRET',
    inventory_service_url='http://inventory',
    inventory_service_token_ref='env:INVENTORY_TOKEN',
    notification_service_url='http://notification',
    notification_service_token_ref='env:NOTIFICATION_TOKEN',
    slack_bot_token_ref='env:SLACK_SECRET',
    smtp_password_ref='env:SMTP_SECRET',
)
async def run():
    http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    container = build_container(settings, Env(), http)
    forbidden = (
        'telescreen.inventory.', 'telescreen.wiring_v14',
        'telescreen.notifications.sender', 'telescreen.notifications.delivery',
        'telescreen.notifications.email', 'telescreen.notifications.slack',
        'telescreen.notifications.tester', 'telescreen.notifications.retention',
        'telescreen.notifications.preferences', 'telescreen.notifications.repository',
        'telescreen.notifications.member_send', 'telescreen.notifications.templates',
        'telescreen.api.routers.notification_',
    )
    assert not tuple(name for name in sys.modules if name.startswith(forbidden))
    await close_container(container)
    await http.aclose()
asyncio.run(run())
"""
    result: subprocess.CompletedProcess[str] = subprocess.run(
        (sys.executable, "-c", program),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_notification_proxy_forwards_public_path_and_filters_headers() -> None:
    """Forward preference updates with user auth and replace caller service credentials."""
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            207,
            content=b"safe response",
            headers={"content-type": "application/json", "set-cookie": "private=1"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as upstream:
        service: ServiceHttp = ServiceHttp(
            upstream, "http://notification", SecretValue("service-secret"), prefix=""
        )
        app: FastAPI = FastAPI()
        app.include_router(
            build_proxy_router(
                service,
                (
                    "/api/auth/me/notification-settings",
                    "/api/notification-templates",
                    "/api/notification-templates/{path:path}",
                ),
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://telescreen"
        ) as client:
            response: httpx.Response = await client.put(
                "/api/auth/me/notification-settings",
                content=b'{"items":[]}',
                headers={
                    "authorization": "Bearer user-token",
                    "x-telescreen-service-token": "caller-value",
                },
            )

    assert response.status_code == 207
    assert response.content == b"safe response"
    assert "set-cookie" not in response.headers
    assert len(requests) == 1
    assert requests[0].url.path == "/api/auth/me/notification-settings"
    assert requests[0].headers["authorization"] == "Bearer user-token"
    assert requests[0].headers["x-telescreen-service-token"] == "service-secret"
    assert requests[0].content == b'{"items":[]}'


@pytest.mark.asyncio
async def test_split_container_binds_proxies_handlers_outbox_and_projection_markers() -> None:
    """Exercise split composition through its producer and durable registrations."""
    harness: SqliteHarness = await make_sqlite_harness()
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        """Record any transport use without reaching an external service."""
        requests.append(request)
        return httpx.Response(404)

    http: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    env: _SplitEnv = _SplitEnv(
        (
            ("FINGERPRINT_SECRET", "f" * 32),
            ("INVENTORY_TOKEN", "i" * 32),
            ("NOTIFICATION_TOKEN", "n" * 32),
        )
    )
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:FINGERPRINT_SECRET",
        inventory_service_url="http://inventory",
        inventory_service_token_ref="env:INVENTORY_TOKEN",
        notification_service_url="http://notification",
        notification_service_token_ref="env:NOTIFICATION_TOKEN",
        slack_bot_token_ref="env:SLACK_SECRET",
        smtp_password_ref="env:SMTP_SECRET",
    )
    container: Container = build_container(settings, env, http, harness.sessions, FakeClock(_NOW))
    try:
        routes: tuple[APIRoute, ...] = tuple(
            route
            for router in container.routers
            for route in router.routes
            if isinstance(route, APIRoute)
        )
        expected_paths: tuple[str, ...] = (
            "/api/auth/me/notification-settings",
            "/api/notification-templates",
            "/api/notification-templates/{path:path}",
            "/api/data/{path:path}",
        )
        assert all(any(route.path == path for route in routes) for path in expected_paths)
        for path in expected_paths:
            path_routes: tuple[APIRoute, ...] = tuple(
                route for route in routes if route.path == path
            )
            assert path_routes
            assert all(
                route.endpoint.__module__ == "telescreen.inventory_service.bureau"
                for route in path_routes
            )

        handlers: tuple[TaskHandler, ...] = tuple(
            handler for _, handler in container.worker.registry.handlers
        )
        assert any(isinstance(handler, NotificationCommandDeliveryHandler) for handler in handlers)
        assert any(isinstance(handler, NotificationDeliveryNoop) for handler in handlers)
        assert any(isinstance(handler, ProjectionRelayHandler) for handler in handlers)
        assert "NotificationDeliveryHandler" not in tuple(
            type(handler).__name__ for handler in handlers
        )

        raw_sample: str = "private-person@example.invalid"
        request: NotificationRequest = NotificationRequest(
            NotificationChannel.EMAIL,
            ("member:unknown",),
            "Notice for private-person@example.invalid",
            "private-person@example.invalid",
            "member:tester",
            record_unreachable=True,
        )
        async with harness.sessions.begin() as session:
            await container.v12.notifications.send(session, request)
        async with harness.sessions() as session:
            outbox_rows: tuple[NotificationOutboxRow, ...] = tuple(
                (await session.scalars(select(NotificationOutboxRow))).all()
            )
            delivery_tasks: tuple[EngineTaskRow, ...] = tuple(
                (
                    await session.scalars(
                        select(EngineTaskRow).where(
                            EngineTaskRow.kind == EngineTaskKind.NOTIFICATION_COMMAND_DELIVERY.value
                        )
                    )
                ).all()
            )
        assert len(outbox_rows) == 1
        assert len(delivery_tasks) == 1
        assert delivery_tasks[0].ref_id == outbox_rows[0].id
        assert delivery_tasks[0].idempotency_key.startswith("notification-command:")
        assert raw_sample not in outbox_rows[0].payload_json

        async with harness.sessions.begin() as session:
            session.add(
                MemberRow(
                    id="split-member-1",
                    handle="split-member",
                    display_name="Before update",
                    active=True,
                    role="admin",
                    email=None,
                    slack_user_id=None,
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
        async with harness.sessions.begin() as session:
            member: MemberRow = await session.get_one(MemberRow, "split-member-1")
            member.display_name = "After update"
        async with harness.sessions() as session:
            markers: tuple[ProjectionChangeRow, ...] = tuple(
                (
                    await session.scalars(
                        select(ProjectionChangeRow).where(
                            ProjectionChangeRow.kind == "directory",
                            ProjectionChangeRow.entity_ref == "member:split-member-1",
                        )
                    )
                ).all()
            )
        assert len(markers) == 2
        assert requests == []
    finally:
        uninstall_projection_markers(harness.sessions)
        await close_container(container)
        await http.aclose()
        await harness.close()


@pytest.mark.asyncio
async def test_split_topology_config_errors_precede_resource_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject partial split configurations before reading secrets or using HTTP."""
    cases: tuple[tuple[TelescreenSettings, str], ...] = (
        (
            TelescreenSettings(
                inventory_service_url="http://inventory",
                inventory_service_token_ref="env:INVENTORY_TOKEN",
            ),
            "inventory and notification service URLs must be configured together",
        ),
        (
            TelescreenSettings(
                inventory_service_url="http://inventory",
                inventory_service_token_ref="env:INVENTORY_TOKEN",
                notification_service_url="http://notification",
            ),
            "notification service token is required in split mode",
        ),
    )
    monkeypatch.setattr("telescreen.wiring.build_primitives", _unexpected_build_primitives)
    for settings, expected_detail in cases:
        requests: list[httpx.Request] = []

        def reply(request: httpx.Request, sink: list[httpx.Request] = requests) -> httpx.Response:
            """Record resource use while remaining offline."""
            sink.append(request)
            return httpx.Response(404)

        http: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(reply))
        env: _SplitEnv = _SplitEnv(())
        try:
            with pytest.raises(ConfigError) as raised:
                build_container(settings, env, http)
            assert raised.value.detail == expected_detail
            assert env.reads == []
            assert requests == []
        finally:
            await http.aclose()
