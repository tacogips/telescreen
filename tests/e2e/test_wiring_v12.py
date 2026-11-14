"""End-to-end coverage for v1.2 container composition and OIDC wiring."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport
from sqlalchemy import select

from telescreen.api.app import create_app
from telescreen.core.config import TelescreenSettings
from telescreen.core.json import json_array, json_object, json_str
from telescreen.core.vocabulary import NotificationChannel, NotificationEvent, OidcProvisioning
from telescreen.db.models.notifications import NotificationRow
from telescreen.identity.signin import SignInService
from telescreen.ports.notifications import NotificationRequest
from telescreen.wiring import Container, build_container, close_container
from tests.identity.oidc_fixtures import (
    AUDIENCE,
    ISSUER,
    base_settings,
    claims_document,
)
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.oidc import TestIdp as LocalIdp


@dataclass(frozen=True)
class _TestEnv:
    """Resolve the test fingerprint key and no external credentials."""

    def get(self, key: str, /) -> str | None:
        """Return only the expected local fingerprint key."""
        return "v" * 32 if key == "TELESCREEN_TEST_KEY" else None


def _claims(now: datetime, subject: str, username: str, email: str, role: str) -> str:
    """Build a Keycloak-shaped role-bearing token payload for a local IdP."""
    return claims_document(
        now,
        (
            ("sub", json_str(subject)),
            ("preferred_username", json_str(username)),
            ("name", json_str(username.title())),
            ("email", json_str(email)),
            (
                "realm_access",
                json_object((("roles", json_array((json_str(f"telescreen-{role}"),))),)),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_container_wires_optional_oidc_routes_and_database_templates() -> None:
    """Wire OIDC only when configured and resolve an API-activated DB template on send."""
    database: SqliteHarness = await make_sqlite_harness()
    idp: LocalIdp = LocalIdp.generate(ISSUER, AUDIENCE)
    outbound: httpx.AsyncClient = httpx.AsyncClient(transport=MockTransport(idp.mock_handler))
    env: _TestEnv = _TestEnv()
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
        oidc=replace(base_settings(), provisioning=OidcProvisioning.CREATE),
    )
    container: Container = build_container(settings, env, outbound, database.sessions)
    try:
        assert isinstance(container.v12.sign_in, SignInService)
        assert container.workflow.templates is container.v12.templates
        app: FastAPI = create_app(container)
        claims: str = _claims(
            datetime.now(UTC), "oidc-admin-subject", "alice", "alice@example.test", "admin"
        )
        admin_token: str = idp.mint(claims)
        viewer_claims: str = _claims(
            datetime.now(UTC), "oidc-viewer-subject", "carol", "carol@example.test", "viewer"
        )
        viewer_token: str = idp.mint(viewer_claims)
        transport: ASGITransport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            config: httpx.Response = await client.get("/api/auth/config")
            assert config.status_code == 200
            assert config.json()["oidc"]["issuer"] == ISSUER
            sign_in: httpx.Response = await client.post(
                "/api/auth/session", headers={"Authorization": f"Bearer {admin_token}"}
            )
            assert sign_in.status_code == 200, sign_in.text
            template_list: httpx.Response = await client.get(
                "/api/notification-templates",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert template_list.status_code == 200, template_list.text
            created: httpx.Response = await client.post(
                "/api/notification-templates",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "name": "integration.db-template",
                    "event_kind": "workflow_notify",
                    "subject": "DB template subject",
                    "body": "DB template body",
                },
            )
            assert created.status_code == 201, created.text
            activated: httpx.Response = await client.post(
                "/api/notification-templates/integration.db-template/activate",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"version": 1},
            )
            assert activated.status_code == 200, activated.text
            viewer_sign_in: httpx.Response = await client.post(
                "/api/auth/session", headers={"Authorization": f"Bearer {viewer_token}"}
            )
            assert viewer_sign_in.status_code == 200, viewer_sign_in.text
            forbidden: httpx.Response = await client.post(
                "/api/notification-templates",
                headers={"Authorization": f"Bearer {viewer_token}"},
                json={
                    "name": "integration.viewer-template",
                    "event_kind": "workflow_notify",
                    "subject": "Denied",
                    "body": "Denied",
                },
            )
            assert forbidden.status_code == 403
        async with container.sessions.begin() as session:
            await container.v12.notifications.send(
                session,
                NotificationRequest(
                    NotificationChannel.EMAIL,
                    ("email:template@example.test",),
                    "",
                    "",
                    "system:test",
                    event=NotificationEvent.WORKFLOW_NOTIFY,
                    template_name="integration.db-template",
                    record_unreachable=True,
                ),
            )
        async with container.sessions() as session:
            row: NotificationRow | None = await session.scalar(select(NotificationRow))
        assert row is not None
        assert row.subject == "DB template subject"
        assert row.body == "DB template body"
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()


@pytest.mark.asyncio
async def test_container_without_oidc_disables_sign_in_and_reports_static_login() -> None:
    """Keep static-token-only operation available when OIDC is not configured."""
    database: SqliteHarness = await make_sqlite_harness()
    outbound: httpx.AsyncClient = httpx.AsyncClient()
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
    )
    container: Container = build_container(settings, _TestEnv(), outbound, database.sessions)
    try:
        assert container.v12.sign_in is None
        assert container.v12.templates is not None
        app: FastAPI = create_app(container)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            config: httpx.Response = await client.get("/api/auth/config")
            session: httpx.Response = await client.post("/api/auth/session")
        assert config.status_code == 200
        assert config.json() == {"oidc": None, "token_login": True}
        assert session.status_code == 404
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()
