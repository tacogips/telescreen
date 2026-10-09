"""Regression coverage for the assembled feature router set."""

from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from telescreen.api.app import create_app
from telescreen.core.config import TelescreenSettings
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import ApiScope
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.wiring import Container, build_container, close_container
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class _TestEnv(EnvSource):
    """Supply only the test fingerprint secret to the composition root."""

    secret: str

    def get(self, key: str, /) -> str | None:
        """Resolve the configured test secret by its name."""
        return self.secret if key == "TELESCREEN_TEST_KEY" else None


@pytest.mark.asyncio
async def test_assembled_feature_list_routes_return_200() -> None:
    """Keep every UI-backed list route mounted and available in the API app."""
    database: SqliteHarness = await make_sqlite_harness()
    env: _TestEnv = _TestEnv("t" * 32)
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
    )
    outbound: httpx.AsyncClient = httpx.AsyncClient()
    container: Container = build_container(settings, env, outbound, database.sessions)
    try:
        async with database.sessions.begin() as session:
            token: CreatedToken = await TokenService(container.audit, container.clock).create(
                session,
                "route-smoke",
                frozenset((ApiScope.ADMIN,)),
                None,
            )
        app: FastAPI = create_app(container)
        transport: ASGITransport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for path in (
                "/api/issues",
                "/api/workflows",
                "/api/runs",
                "/api/police/workers",
                "/api/collectors",
                "/api/escalation-policies",
                "/api/map-jobs",
            ):
                response: httpx.Response = await client.get(
                    path, headers={"Authorization": f"Bearer {token.token}"}
                )
                assert response.status_code == 200, (path, response.text)
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()
