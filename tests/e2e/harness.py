"""Shared hermetic harness for assembled application flow tests."""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from telescreen.api.app import create_app
from telescreen.core.clock import FakeClock
from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import ApiScope
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.wiring import Container, build_container, close_container
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class PairEnv(EnvSource):
    """Immutable environment source backed by a tuple of key/value pairs."""

    values: tuple[tuple[str, str], ...]

    def get(self, key: str, /) -> str | None:
        """Return an exact environment entry, if present."""
        return next((value for name, value in self.values if name == key), None)


@dataclass(frozen=True)
class RecordedRequest:
    """Captured outbound request with its body hidden from repr."""

    method: str
    host: str
    path: str
    body: bytes = field(repr=False)


class HostHandler(Protocol):
    """Handle one outbound request for a mock host."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Return the deterministic response for a request."""


@dataclass
class OutboundRecorder:
    """Record outbound traffic and route it to host-specific mock handlers."""

    requests: list[RecordedRequest] = field(default_factory=list)
    handlers: list[tuple[str, HostHandler]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        """Create a transport that captures and dispatches every request."""

        def dispatch(request: httpx.Request) -> httpx.Response:
            body: bytes = request.content
            host: str = request.url.host
            self.requests.append(RecordedRequest(request.method, host, request.url.path, body))
            handler: HostHandler | None = next(
                (candidate for registered, candidate in self.handlers if registered == host), None
            )
            if handler is None:
                self.failures.append(host)
                return httpx.Response(599, request=request)
            return handler(request)

        return httpx.MockTransport(dispatch)


@dataclass
class E2EWorld:
    """Own the assembled app, fake clock, SQLite state and HTTP clients."""

    database: SqliteHarness
    clock: FakeClock
    container: Container
    client: httpx.AsyncClient
    outbound: OutboundRecorder
    admin_token: str = field(repr=False)
    tmp: Path = field(repr=False)
    responses: list[str] = field(default_factory=list, repr=False)

    async def drain(self, rounds: int = 50, step: timedelta = timedelta(seconds=5)) -> None:
        """Run due work and advance deterministic time until the queue is idle twice."""
        idle_rounds: int = 0
        for _ in range(rounds):
            processed: int = await self.container.worker.run_until_idle()
            self.clock.advance(step)
            idle_rounds = idle_rounds + 1 if processed == 0 else 0
            if idle_rounds == 2:
                return
        raise AssertionError(f"engine did not become idle after {rounds} rounds")

    async def close(self) -> None:
        """Close app-owned resources and the injected HTTP client and database."""
        await close_container(self.container)
        await self.client.aclose()
        await self.database.close()


async def make_world(
    tmp: Path,
    extra_env: tuple[tuple[str, str], ...],
    outbound: OutboundRecorder,
) -> E2EWorld:
    """Build the real container and API over hermetic transports and SQLite."""
    base_env: tuple[tuple[str, str], ...] = (
        ("TELESCREEN_DATABASE_URL", "sqlite+aiosqlite:///:memory:"),
        ("TELESCREEN_FINGERPRINT_KEY", "env:E2E_FP"),
        ("E2E_FP", "e2e-fingerprint-" + "x" * 40),
        ("TELESCREEN_EVIDENCE_DIR", str(tmp / "evidence")),
        ("TELESCREEN_GITHUB_API_URL", "https://github.example.invalid/api/v3"),
    )
    env: PairEnv = PairEnv(base_env + extra_env)
    settings: TelescreenSettings = load_settings(env)
    database: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 1, tzinfo=UTC))
    external_http: httpx.AsyncClient = httpx.AsyncClient(transport=outbound.transport())
    container: Container = build_container(settings, env, external_http, database.sessions, clock)
    container.worker.concurrency = 1
    token: CreatedToken
    async with database.sessions.begin() as session:
        token = await TokenService(container.audit, clock).create(
            session, "e2e-admin", frozenset((ApiScope.ADMIN, ApiScope.READ, ApiScope.WRITE)), None
        )
    app: FastAPI = create_app(container)
    client: AsyncClient = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    tmp.mkdir(parents=True, exist_ok=True)
    return E2EWorld(database, clock, container, client, outbound, token.token, tmp)


def bearer(token: str) -> tuple[tuple[str, str], ...]:
    """Return a bearer authorization header."""
    return (("Authorization", f"Bearer {token}"),)


def github_signature(secret: str, body: bytes) -> str:
    """Return the GitHub SHA-256 webhook signature for exact body bytes."""
    digest: str = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def admin_post(world: E2EWorld, path: str, payload_json_text: str) -> httpx.Response:
    """POST an already-serialized JSON payload through the assembled API."""
    response: httpx.Response = await world.client.post(
        path,
        content=payload_json_text,
        headers=bearer(world.admin_token) + (("Content-Type", "application/json"),),
    )
    world.responses.append(response.text)
    return response
