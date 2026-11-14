"""Real-container helpers for the raw-value leak matrix."""

import base64
import io
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Request, Response

from telescreen.api.app import create_app
from telescreen.core.clock import FakeClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.oidc_settings import OidcSettings
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import ApiScope
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.masking.log_masking import MaskingFormatter
from telescreen.wiring import Container, build_container, close_container
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.oidc import TestIdp
from tests.support.samples import Sample, all_samples

CANARY_FILENAME: str = "leakcanary-" + "fname-7f3a" + ".txt"
CANARY_EMAIL: str = "owner-canary" + "@" + "example.com"


@dataclass(frozen=True)
class PairEnv(EnvSource):
    """Resolve named test secrets without changing process environment."""

    pairs: tuple[tuple[str, str], ...] = field(repr=False)

    def get(self, key: str, /) -> str | None:
        """Return one configured environment value."""
        return next((value for name, value in self.pairs if name == key), None)


@dataclass
class Recorder:
    """Capture outbound HTTP, API responses and canned LLM output."""

    requests: list[tuple[str, bytes]] = field(default_factory=list)
    responses: list[str] = field(default_factory=list)
    response_paths: list[str] = field(default_factory=list)
    llm_responses: list[str] = field(default_factory=list)
    clean_content_paths: set[str] = field(default_factory=set)
    missing_content_paths: set[str] = field(default_factory=set)
    failing_hosts: set[str] = field(default_factory=set)
    idp: TestIdp | None = None

    async def handle(self, request: Request) -> Response:
        """Record a request and return a deterministic provider response."""
        body: bytes = await request.aread()
        self.requests.append((str(request.url), body))
        if self.idp is not None and request.url.host == urlsplit(self.idp.issuer).hostname:
            return self.idp.mock_handler(request)
        if request.url.host in self.failing_hosts:
            return Response(500, request=request)
        if "api.anthropic.com" in request.url.host:
            answer: str = self.llm_responses.pop(0) if self.llm_responses else "{}"
            return Response(
                200,
                json={
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-5-5",
                    "content": [{"type": "text", "text": answer}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
                request=request,
            )
        if "api.github.com" in request.url.host:
            if "/contents/" in request.url.path:
                if request.url.path in self.missing_content_paths:
                    return Response(404, request=request)
                if request.url.path.endswith("/binary.bin"):
                    return Response(
                        200,
                        json={
                            "content": base64.b64encode(b"\x00\xff").decode("ascii"),
                            "encoding": "base64",
                            "size": 2,
                            "sha": "github-binary-sha",
                        },
                        request=request,
                    )
                content: str = (
                    "secrets/ " + CANARY_EMAIL
                    if request.url.path.endswith("/CODEOWNERS")
                    else "safe test content"
                    if request.url.path in self.clean_content_paths
                    else _github_content()
                )
                return Response(
                    200,
                    json={
                        "content": base64.b64encode(content.encode()).decode("ascii"),
                        "encoding": "base64",
                        "size": len(content.encode()),
                        "sha": "github-content-sha",
                    },
                    request=request,
                )
            if request.url.path.endswith("/repos/sample/repo"):
                return Response(
                    200,
                    json={
                        "id": 7,
                        "name": "repo",
                        "full_name": "sample/repo",
                        "default_branch": "main",
                        "private": False,
                        "visibility": "public",
                        "fork": False,
                        "owner": {"login": "sample"},
                    },
                    request=request,
                )
            return Response(200, json={"files": []}, request=request)
        return Response(200, json={"ok": True}, request=request)


@dataclass
class LeakWorld:
    """Own a full API container, test client, and its cleanup resources."""

    database: SqliteHarness
    clock: FakeClock
    container: Container
    app: FastAPI
    client: AsyncClient
    recorder: Recorder
    tmp: Path
    log_buffer: io.StringIO
    log_handler: logging.StreamHandler[io.StringIO]
    outbound: httpx.AsyncClient
    admin_token: str
    ingest_token: str

    async def drain(self) -> None:
        """Run queued work while fake-clock delays remain, with a finite bound."""
        for _ in range(24):
            processed: int = await self.container.worker.run_until_idle()
            if processed == 0:
                self.clock.advance(timedelta(seconds=5))
                processed = await self.container.worker.run_until_idle()
                if processed == 0:
                    return

    async def close(self) -> None:
        """Release clients and database resources owned by this world."""
        await self.client.aclose()
        await close_container(self.container)
        await self.database.close()
        logging.getLogger().removeHandler(self.log_handler)


async def make_leak_world(
    tmp: Path, with_llm: bool, oidc: OidcSettings | None = None, idp: TestIdp | None = None
) -> LeakWorld:
    """Build a real Telescreen container with only declared test seams."""
    database: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 10, tzinfo=UTC))
    recorder: Recorder = Recorder(idp=idp)
    log_buffer: io.StringIO = io.StringIO()
    buffer_handler: logging.StreamHandler[io.StringIO] = logging.StreamHandler(log_buffer)
    root: logging.Logger = logging.getLogger()
    prior_handlers: tuple[logging.Handler, ...] = tuple(root.handlers)
    root.handlers.insert(0, buffer_handler)
    env_pairs: list[tuple[str, str]] = [
        ("TELESCREEN_TEST_KEY", "fingerprint-key-placeholder-000000000000"),
        ("TELESCREEN_SLACK_TOKEN", "slack-token-placeholder"),
        ("TELESCREEN_POLICE_TOKEN", "police-enrollment-placeholder"),
        ("TELESCREEN_GITHUB_SECRET", "github-webhook-placeholder"),
        ("TELESCREEN_GITHUB_TOKEN", "github-api-placeholder"),
    ]
    env_pairs.extend((("TELESCREEN_ANTHROPIC_KEY", "anthropic-placeholder"),) if with_llm else ())
    env: PairEnv = PairEnv(tuple(env_pairs))
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
        slack_bot_token_ref="env:TELESCREEN_SLACK_TOKEN",
        police_enrollment_token_ref="env:TELESCREEN_POLICE_TOKEN",
        github_webhook_secret_ref="env:TELESCREEN_GITHUB_SECRET",
        anthropic_api_key_ref="env:TELESCREEN_ANTHROPIC_KEY" if with_llm else None,
        evidence_dir=str(tmp / "evidence"),
        owner_routing=True,
        default_team="security",
        digest_cron="* * * * *",
        health_recipients=("team:security",),
        health_failure_threshold=3,
        oidc=oidc,
    )
    transport: httpx.AsyncClient = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder.handle),
        event_hooks={"response": [_record_response(recorder)]},
    )
    container: Container = build_container(settings, env, transport, database.sessions, clock)
    container.worker.concurrency = 1
    assert isinstance(buffer_handler.formatter, MaskingFormatter)
    root.handlers[:] = [buffer_handler, *prior_handlers]
    app: FastAPI = create_app(container)
    await container.ensure_indexes()
    client: AsyncClient = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        event_hooks={"response": [_record_response(recorder)]},
    )
    async with database.sessions.begin() as session:
        admin: CreatedToken = await TokenService(container.audit, clock).create(
            session, "leak-admin", frozenset((ApiScope.ADMIN,)), None
        )
        ingest: CreatedToken = await TokenService(container.audit, clock).create(
            session, "leak-ingest", frozenset((ApiScope.INGEST,)), None
        )
    security_team: Response = await client.post(
        "/api/teams",
        headers=auth(admin.token),
        json={"slug": "security", "name": "Security", "email": CANARY_EMAIL},
    )
    assert security_team.status_code == 201, security_team.text
    return LeakWorld(
        database,
        clock,
        container,
        app,
        client,
        recorder,
        tmp,
        log_buffer,
        buffer_handler,
        transport,
        admin.token,
        ingest.token,
    )


def _record_response(recorder: Recorder) -> Callable[[Response], Awaitable[None]]:
    """Create a response hook that eagerly buffers safe response text."""

    async def record(response: Response) -> None:
        await response.aread()
        recorder.response_paths.append(response.request.url.path)
        recorder.responses.append(response.text)

    return record


def auth(token: str) -> tuple[tuple[str, str], ...]:
    """Build one bearer authorization header."""
    return (("Authorization", f"Bearer {token}"),)


def _github_content() -> str:
    """Assemble runtime samples served by the canned GitHub contents endpoint."""
    samples: tuple[Sample, ...] = all_samples()
    access_id: str = next(sample.value for sample in samples if sample.name == "aws_access_key_id")
    access_secret: str = next(
        sample.value for sample in samples if sample.name == "aws_secret_access_key"
    )
    private_key: str = next(sample.value for sample in samples if sample.name == "pem_private_key")
    jwt: str = next(sample.value for sample in samples if sample.name == "jwt_token")
    return "\n".join((access_id, access_secret, private_key, jwt))
