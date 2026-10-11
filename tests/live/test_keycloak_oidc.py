"""Live Keycloak role mapping and Authorization Code + PKCE smoke checks."""

import base64
import hashlib
import html
import os
import re
import secrets
from pathlib import Path
from urllib.parse import unquote, urlencode, urljoin, urlparse

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from telescreen.api.app import create_app
from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.secrets import EnvSource
from telescreen.wiring import Container, build_container, close_container
from tests.e2e.harness import PairEnv
from tests.live.conftest import require_live_variables
from tests.support.db import SqliteHarness, make_sqlite_harness


@pytest.mark.live
@pytest.mark.asyncio
async def test_keycloak_direct_grants_map_roles_and_pkce_authorize(tmp_path: Path) -> None:
    """Verify realm users through the real bearer validator and inspect PKCE redirect."""
    require_live_variables("TELESCREEN_TEST_KEYCLOAK_URL")
    keycloak_url: str = os.environ["TELESCREEN_TEST_KEYCLOAK_URL"].rstrip("/")
    issuer: str = f"{keycloak_url}/realms/telescreen"
    database: SqliteHarness = await make_sqlite_harness()
    outbound: httpx.AsyncClient = httpx.AsyncClient(timeout=20)
    values: tuple[tuple[str, str], ...] = (
        ("TELESCREEN_DATABASE_URL", "sqlite+aiosqlite:///:memory:"),
        ("TELESCREEN_FINGERPRINT_KEY", "env:TELESCREEN_TEST_FINGERPRINT"),
        ("TELESCREEN_TEST_FINGERPRINT", "live-test-fingerprint-key-000000000000"),
        ("TELESCREEN_OIDC_ISSUER", issuer),
        ("TELESCREEN_OIDC_CLIENT_ID", "telescreen-ui"),
        ("TELESCREEN_OIDC_AUDIENCE", "telescreen-ui"),
        ("TELESCREEN_OIDC_PROVISIONING", "create"),
        ("TELESCREEN_OIDC_TEAM_MAP", "security=secops,dev=dev,ghe-admin=ghe-admin"),
        ("TELESCREEN_EVIDENCE_DIR", str(tmp_path / "evidence")),
    )
    env: EnvSource = PairEnv(values)
    settings: TelescreenSettings = load_settings(env)
    container: Container = build_container(settings, env, outbound, database.sessions)
    app_client: AsyncClient | None = None
    try:
        app_client = AsyncClient(
            transport=ASGITransport(app=create_app(container)), base_url="http://telescreen.test"
        )
        await container.ensure_indexes()
        async with httpx.AsyncClient(timeout=20) as client:
            alice_id_token: str | None = None
            carol_access_token: str | None = None
            for username, role in (
                ("alice", "admin"),
                ("bob", "responder"),
                ("carol", "viewer"),
            ):
                token_response: httpx.Response = await client.post(
                    f"{issuer}/protocol/openid-connect/token",
                    data={
                        "grant_type": "password",
                        "client_id": "telescreen-ui",
                        "username": username,
                        "password": f"{username}-dev-pass",
                        "scope": "openid profile email",
                    },
                )
                assert token_response.status_code == 200
                access_token: str = token_response.json()["access_token"]
                if username == "alice":
                    alice_id_token = token_response.json()["id_token"]
                if username == "carol":
                    carol_access_token = access_token
                session: httpx.Response = await app_client.post(
                    "/api/auth/session", headers={"Authorization": f"Bearer {access_token}"}
                )
                assert session.status_code == 200, session.text
                assert session.json()["role"] == role
            assert alice_id_token is not None and carol_access_token is not None
            id_token_session: httpx.Response = await app_client.post(
                "/api/auth/session",
                headers={"Authorization": f"Bearer {alice_id_token}"},
            )
            assert id_token_session.status_code == 401
            viewer_write: httpx.Response = await app_client.post(
                "/api/notification-templates",
                headers={"Authorization": f"Bearer {carol_access_token}"},
                json={
                    "name": "live.viewer-denied",
                    "event_kind": "workflow_notify",
                    "subject": "Denied",
                    "body": "Denied",
                },
            )
            assert viewer_write.status_code == 403
            dave_response: httpx.Response = await client.post(
                f"{issuer}/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "telescreen-ui",
                    "username": "dave",
                    "password": "dave-dev-pass",
                    "scope": "openid profile email",
                },
            )
            assert dave_response.status_code == 200
            dave_token: str = dave_response.json()["access_token"]
            no_role: httpx.Response = await app_client.post(
                "/api/auth/session", headers={"Authorization": f"Bearer {dave_token}"}
            )
            assert no_role.status_code == 403
            assert no_role.json()["error"]["code"] == "no_role"

            verifier: str = secrets.token_urlsafe(48)
            challenge: str = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode("ascii")
                .rstrip("=")
            )
            authorize: httpx.Response = await client.get(
                f"{issuer}/protocol/openid-connect/auth",
                params={
                    "client_id": "telescreen-ui",
                    "response_type": "code",
                    "scope": "openid profile email",
                    "redirect_uri": "http://127.0.0.1:5174/auth/callback",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": secrets.token_urlsafe(24),
                },
                follow_redirects=True,
            )
            assert authorize.status_code == 200
            form_match: re.Match[str] | None = re.search(
                r'<form[^>]+action="([^"]+)"', authorize.text
            )
            assert form_match is not None
            form_action: str = urljoin(str(authorize.url), html.unescape(form_match.group(1)))
            assert urlparse(form_action).netloc == urlparse(keycloak_url).netloc
            form_fields: list[tuple[str, str]] = []
            for input_tag in re.findall(r"<input[^>]*>", authorize.text):
                name_match: re.Match[str] | None = re.search(r'name="([^"]+)"', input_tag)
                value_match: re.Match[str] | None = re.search(r'value="([^"]*)"', input_tag)
                if name_match is not None:
                    form_fields.append(
                        (
                            name_match.group(1),
                            html.unescape(value_match.group(1)) if value_match is not None else "",
                        )
                    )
            pkce_username: str = "alice"
            form_fields.extend(
                (
                    ("username", pkce_username),
                    ("password", f"{pkce_username}-dev-pass"),
                )
            )
            login_request: httpx.Request = client.build_request(
                "POST",
                form_action,
                content=urlencode(form_fields),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            login: httpx.Response = await client.send(login_request, follow_redirects=False)
            assert login.status_code in (302, 303)
            location: str = login.headers["location"]
            code_match: re.Match[str] | None = re.search(r"[?&]code=([^&]+)", location)
            assert code_match is not None
            code: str = unquote(code_match.group(1))
            exchanged: httpx.Response = await client.post(
                f"{issuer}/protocol/openid-connect/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": "telescreen-ui",
                    "redirect_uri": "http://127.0.0.1:5174/auth/callback",
                    "code": code,
                    "code_verifier": verifier,
                },
            )
            assert exchanged.status_code == 200
            pkce_token: str = exchanged.json()["access_token"]
            pkce_session: httpx.Response = await app_client.post(
                "/api/auth/session", headers={"Authorization": f"Bearer {pkce_token}"}
            )
            assert pkce_session.status_code == 200
            assert pkce_session.json()["role"] == "admin"
    finally:
        if app_client is not None:
            await app_client.aclose()
        await close_container(container)
        await database.close()
        await outbound.aclose()
