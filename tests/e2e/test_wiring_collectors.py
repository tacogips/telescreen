"""Behavioral coverage for built-in collector composition and webhook secrets."""

import asyncio
import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from telescreen.api.app import create_app
from telescreen.api.routers.collectors import WebhookSecrets
from telescreen.collectors.github.adapter import GitHubAdapter
from telescreen.collectors.google.adapter import GoogleAdapter
from telescreen.collectors.registry import CollectorRegistry
from telescreen.core.clock import FakeClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError
from telescreen.core.secrets import EnvSource, SecretValue
from telescreen.core.vocabulary import CollectorKind, TriggerMode
from telescreen.ports.collectors import CollectorAdapter, CollectorConfig, GitHubSelector
from telescreen.wiring import Container, build_container, close_container
from telescreen.wiring_collectors import (
    build_collector_registry,
    build_webhook_secrets,
    drive_channel_key,
    github_web_host,
)
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class _TestEnv(EnvSource):
    """Resolve test-only values without reading process environment."""

    values: tuple[tuple[str, str], ...] = field(repr=False)

    def get(self, key: str, /) -> str | None:
        """Return the matching configured test value."""
        return next((value for name, value in self.values if name == key), None)


def _settings(
    slack_signing_secret_ref: str | None = None,
    github_webhook_secret_ref: str | None = None,
) -> TelescreenSettings:
    """Build test settings with an isolated in-memory database."""
    return TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
        slack_signing_secret_ref=slack_signing_secret_ref,
        github_webhook_secret_ref=github_webhook_secret_ref,
    )


async def _container(
    settings: TelescreenSettings, env: _TestEnv
) -> tuple[Container, SqliteHarness, httpx.AsyncClient]:
    """Create a test composition root over in-memory SQLite."""
    database: SqliteHarness = await make_sqlite_harness()
    outbound: httpx.AsyncClient = httpx.AsyncClient()
    container: Container = build_container(settings, env, outbound, database.sessions)
    return container, database, outbound


@pytest.mark.asyncio
async def test_registry_registers_five_kinds_and_shares_drive_key() -> None:
    """Register each built-in adapter and sweep capability with one channel key."""
    raw_key: SecretValue = SecretValue("t" * 32)
    channel_key: bytes = drive_channel_key(raw_key)
    assert channel_key == drive_channel_key(raw_key)
    assert len(channel_key) == 32
    assert channel_key != raw_key.value.encode("utf-8")
    env: _TestEnv = _TestEnv((("TELESCREEN_TEST_KEY", raw_key.value),))
    settings: TelescreenSettings = _settings()
    outbound: httpx.AsyncClient = httpx.AsyncClient()
    registry: CollectorRegistry = build_collector_registry(
        settings, env, outbound, FakeClock(datetime(2026, 10, 9, tzinfo=UTC)), channel_key
    )
    secrets: WebhookSecrets = build_webhook_secrets(settings, env, channel_key)
    try:
        for kind in (
            CollectorKind.SLACK,
            CollectorKind.GDRIVE,
            CollectorKind.GDOCS,
            CollectorKind.GSHEETS,
            CollectorKind.GITHUB,
        ):
            assert registry.adapter(kind) is not None
            assert registry.sweeper(kind) is not None
        drive: CollectorAdapter = registry.adapter(CollectorKind.GDRIVE)
        assert isinstance(drive, GoogleAdapter)
        assert drive.channel_token_key == secrets.gdrive.channel_token_key
    finally:
        await outbound.aclose()

    container: Container
    database: SqliteHarness
    container_http: httpx.AsyncClient
    container, database, container_http = await _container(settings, env)
    try:
        for kind in (
            CollectorKind.SLACK,
            CollectorKind.GDRIVE,
            CollectorKind.GDOCS,
            CollectorKind.GSHEETS,
            CollectorKind.GITHUB,
        ):
            assert container.collector_registry.adapter(kind) is not None
            assert container.collector_registry.sweeper(kind) is not None
        container_drive: CollectorAdapter = container.collector_registry.adapter(
            CollectorKind.GDRIVE
        )
        assert isinstance(container_drive, GoogleAdapter)
        assert container_drive.channel_token_key == drive_channel_key(raw_key)

        drive_channel_id: str = "test-channel"
        drive_token: str = hmac.new(
            drive_channel_key(raw_key),
            b"drive-channel\0" + drive_channel_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        app: FastAPI = create_app(container)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response: httpx.Response = await client.post(
                "/api/collectors/gdrive/webhook",
                content=b"{}",
                headers={
                    "X-Goog-Channel-ID": drive_channel_id,
                    "X-Goog-Channel-Token": drive_token,
                    "X-Goog-Resource-State": "sync",
                },
            )
        assert response.status_code == 200
    finally:
        await close_container(container)
        await database.close()
        await container_http.aclose()


@pytest.mark.asyncio
async def test_unset_github_webhook_secret_fails_closed() -> None:
    """Reject a GitHub webhook when no signing secret reference is configured."""
    env: _TestEnv = _TestEnv((("TELESCREEN_TEST_KEY", "t" * 32),))
    container: Container
    database: SqliteHarness
    outbound: httpx.AsyncClient
    container, database, outbound = await _container(_settings(), env)
    try:
        app: FastAPI = create_app(container)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response: httpx.Response = await client.post(
                "/api/collectors/github/webhook",
                content=b"{}",
                headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=bad"},
            )
        assert response.status_code == 401
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()


@pytest.mark.asyncio
async def test_configured_webhook_secrets_accept_valid_signatures() -> None:
    """Accept correct GitHub and Slack signatures and reject incorrect ones."""
    now: datetime = datetime.now(UTC)
    github_secret: str = "github-signing-secret-for-tests"
    slack_secret: str = "slack-signing-secret-for-tests"
    env: _TestEnv = _TestEnv(
        (
            ("TELESCREEN_TEST_KEY", "t" * 32),
            ("TELESCREEN_GITHUB_HOOK", github_secret),
            ("TELESCREEN_SLACK_HOOK", slack_secret),
        )
    )
    settings: TelescreenSettings = _settings(
        slack_signing_secret_ref="env:TELESCREEN_SLACK_HOOK",
        github_webhook_secret_ref="env:TELESCREEN_GITHUB_HOOK",
    )
    container: Container
    database: SqliteHarness
    outbound: httpx.AsyncClient
    container, database, outbound = await _container(settings, env)
    github_body: bytes = b"{}"
    github_digest: str = hmac.new(
        github_secret.encode("utf-8"), github_body, hashlib.sha256
    ).hexdigest()
    slack_timestamp: str = str(int(now.timestamp()))
    slack_body: bytes = b'{"type":"url_verification","challenge":"challenge-value"}'
    slack_base: bytes = b"v0:" + slack_timestamp.encode() + b":" + slack_body
    slack_signature: str = (
        "v0=" + hmac.new(slack_secret.encode("utf-8"), slack_base, hashlib.sha256).hexdigest()
    )
    try:
        app: FastAPI = create_app(container)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            github_good: httpx.Response = await client.post(
                "/api/collectors/github/webhook",
                content=github_body,
                headers={
                    "X-GitHub-Event": "ping",
                    "X-Hub-Signature-256": f"sha256={github_digest}",
                },
            )
            github_bad: httpx.Response = await client.post(
                "/api/collectors/github/webhook",
                content=github_body,
                headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": "sha256=bad"},
            )
            slack_good: httpx.Response = await client.post(
                "/api/collectors/slack/webhook",
                content=slack_body,
                headers={
                    "X-Slack-Request-Timestamp": slack_timestamp,
                    "X-Slack-Signature": slack_signature,
                },
            )
            slack_bad: httpx.Response = await client.post(
                "/api/collectors/slack/webhook",
                content=slack_body,
                headers={
                    "X-Slack-Request-Timestamp": slack_timestamp,
                    "X-Slack-Signature": "v0=bad",
                },
            )
        assert github_good.status_code == 200
        assert github_bad.status_code == 401
        assert slack_good.status_code == 200
        assert slack_good.text == "challenge-value"
        assert slack_bad.status_code == 401
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()


@pytest.mark.asyncio
async def test_missing_webhook_secret_reference_raises_config_error() -> None:
    """Fail helper and container startup for unavailable webhook references."""
    env: _TestEnv = _TestEnv((("TELESCREEN_TEST_KEY", "t" * 32),))
    settings: TelescreenSettings = _settings(github_webhook_secret_ref="env:MISSING")
    with pytest.raises(ConfigError):
        build_webhook_secrets(settings, env, b"k" * 32)
    database: SqliteHarness = await make_sqlite_harness()
    outbound: httpx.AsyncClient = httpx.AsyncClient()
    try:
        with pytest.raises(ConfigError):
            build_container(settings, env, outbound, database.sessions)
    finally:
        await database.close()
        await outbound.aclose()


def test_github_factory_resolves_config_credentials_lazily_and_safely() -> None:
    """Report only a static error when a GitHub config has no credential reference."""
    env: _TestEnv = _TestEnv((("TELESCREEN_TEST_KEY", "t" * 32),))
    settings: TelescreenSettings = _settings()
    outbound: httpx.AsyncClient = httpx.AsyncClient()
    registry: CollectorRegistry = build_collector_registry(
        settings, env, outbound, FakeClock(datetime(2026, 10, 9, tzinfo=UTC)), b"k" * 32
    )
    adapter: CollectorAdapter = registry.adapter(CollectorKind.GITHUB)
    assert isinstance(adapter, GitHubAdapter)
    config: CollectorConfig = CollectorConfig(
        "config-id",
        "github-test",
        CollectorKind.GITHUB,
        TriggerMode.EVENT,
        None,
        None,
        16,
        GitHubSelector("owner", (), (), ()),
        None,
        True,
    )
    with pytest.raises(ConfigError) as error:
        adapter.api_factory(config)
    assert error.value.detail == "github credential is not configured"
    assert "MISSING" not in str(error.value)
    asyncio.run(outbound.aclose())


def test_github_host_maps_public_api_and_preserves_enterprise_host() -> None:
    """Derive resource locator hosts from public and enterprise API URLs."""
    assert github_web_host("https://api.github.com") == "github.com"
    assert github_web_host("https://ghe.example.invalid/api/v3") == "ghe.example.invalid"
