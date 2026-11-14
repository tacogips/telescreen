"""Live v1.3 alert delivery through Slack and Mailpit."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import TypeAdapter
from sqlalchemy import select

from telescreen.api.app import create_app
from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import ApiScope, NotificationStatus
from telescreen.db.models import AlertFireRow, NotificationRow
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.wiring import Container, build_container, close_container
from tests.live.conftest import require_live_variables
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import aws_access_key_id


@dataclass(frozen=True)
class _LiveEnv(EnvSource):
    """Resolve live Slack credentials and the local fingerprint key."""

    values: tuple[tuple[str, str], ...] = field(repr=False)

    def get(self, key: str, /) -> str | None:
        """Return only configured test environment entries."""
        return next((value for name, value in self.values if name == key), None)


@dataclass(frozen=True)
class _MailpitMessage:
    """Mailpit search result identifier used by this smoke test."""

    ID: str


@dataclass(frozen=True)
class _MailpitSearch:
    """Typed Mailpit search response."""

    messages: list[_MailpitMessage]


_HISTORY_SCOPES: frozenset[str] = frozenset(
    ("channels:history", "groups:history", "im:history", "mpim:history")
)


@pytest.mark.live
@pytest.mark.asyncio
async def test_alert_live_delivers_once_to_slack_and_mailpit(tmp_path: Path) -> None:
    """Deliver one issue-created alert to the configured live Slack and email targets."""
    require_live_variables(
        "TELESCREEN_TEST_SLACK_BOT_TOKEN",
        "TELESCREEN_TEST_SLACK_CHANNEL",
        "TELESCREEN_TEST_SMTP_HOST",
        "TELESCREEN_TEST_SMTP_PORT",
        "TELESCREEN_TEST_MAILPIT_URL",
        "TELESCREEN_TEST_EMAIL_TO",
    )
    slack_token: str = os.environ["TELESCREEN_TEST_SLACK_BOT_TOKEN"]
    slack_channel: str = os.environ["TELESCREEN_TEST_SLACK_CHANNEL"]
    smtp_host: str = os.environ["TELESCREEN_TEST_SMTP_HOST"]
    smtp_port: str = os.environ["TELESCREEN_TEST_SMTP_PORT"]
    mailpit_url: str = os.environ["TELESCREEN_TEST_MAILPIT_URL"].rstrip("/")
    recipient: str = os.environ["TELESCREEN_TEST_EMAIL_TO"]
    run_id: str = uuid4().hex
    rule_name: str = f"live-alert-{run_id}"
    sample: str = aws_access_key_id().value
    database: SqliteHarness = await make_sqlite_harness()
    env: EnvSource = _LiveEnv(
        (
            ("TELESCREEN_DATABASE_URL", "sqlite+aiosqlite:///:memory:"),
            ("TELESCREEN_FINGERPRINT_KEY", "env:TELESCREEN_LIVE_FINGERPRINT"),
            ("TELESCREEN_LIVE_FINGERPRINT", "live-test-fingerprint-key-000000000000"),
            ("TELESCREEN_SMTP_HOST", smtp_host),
            ("TELESCREEN_SMTP_PORT", smtp_port),
            ("TELESCREEN_SMTP_STARTTLS", "false"),
            ("TELESCREEN_SMTP_FROM", "telescreen@example.test"),
            ("TELESCREEN_SLACK_BOT_TOKEN", "env:TELESCREEN_TEST_SLACK_BOT_TOKEN"),
            ("TELESCREEN_TEST_SLACK_BOT_TOKEN", slack_token),
            ("TELESCREEN_EVIDENCE_DIR", str(tmp_path / "evidence")),
        )
    )
    settings: TelescreenSettings = load_settings(env)
    outbound: httpx.AsyncClient = httpx.AsyncClient(timeout=20)
    container: Container = build_container(settings, env, outbound, database.sessions)
    client: httpx.AsyncClient | None = None
    try:
        async with container.sessions.begin() as session:
            tokens: TokenService = TokenService(container.audit, container.clock)
            admin: CreatedToken = await tokens.create(
                session,
                "live-alert-admin",
                frozenset((ApiScope.ADMIN, ApiScope.READ, ApiScope.WRITE)),
                None,
            )
            ingest: CreatedToken = await tokens.create(
                session, "live-alert-ingest", frozenset((ApiScope.INGEST,)), None
            )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(container)),
            base_url="http://telescreen.test",
        )
        collector: httpx.Response = await client.post(
            "/api/collectors",
            headers={"Authorization": f"Bearer {admin.token}"},
            json={
                "name": f"live-alert-{run_id}",
                "kind": "custom",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": json.dumps({"paths": ["**"]}),
                "credential_ref": None,
                "enabled": True,
            },
        )
        assert collector.status_code == 200
        rule: httpx.Response = await client.post(
            "/api/alert-rules",
            headers={"Authorization": f"Bearer {admin.token}"},
            json={
                "name": rule_name,
                "trigger": {"kind": "frequency", "threshold": 1, "window_minutes": 60},
                "actions": {
                    "targets": [
                        {"recipients": [f"slack_channel:{slack_channel}"], "channels": ["slack"]},
                        {"recipients": [f"email:{recipient}"], "channels": ["email"]},
                    ]
                },
                "action_interval_minutes": 60,
            },
        )
        assert rule.status_code == 201
        rule_id: str = rule.json()["id"]
        scan: httpx.Response = await client.post(
            "/api/ingest/v1/scan",
            headers={"Authorization": f"Bearer {ingest.token}"},
            json={
                "source": f"custom:live-alert-{run_id}",
                "resources": [
                    {
                        "locator": f"repo/{run_id}.env",
                        "title": "Live alert delivery verification",
                        "complete": True,
                        "segments": [{"locator": "L1", "text": f"key={sample}"}],
                    }
                ],
            },
        )
        assert scan.status_code == 200
        issue_ids: list[str] = scan.json()["issue_ids"]
        assert issue_ids
        issue_id: str = issue_ids[0]
        issue_response: httpx.Response = await client.get(
            f"/api/issues/{issue_id}", headers={"Authorization": f"Bearer {admin.token}"}
        )
        assert issue_response.status_code == 200
        short_id: str = issue_response.json()["short_id"]
        await container.worker.run_until_idle()
        async with container.sessions() as session:
            fires: tuple[AlertFireRow, ...] = tuple(
                await session.scalars(select(AlertFireRow).where(AlertFireRow.rule_id == rule_id))
            )
            notifications: tuple[NotificationRow, ...] = tuple(
                await session.scalars(
                    select(NotificationRow).where(NotificationRow.issue_id == issue_id)
                )
            )
        assert len(fires) == 1 and fires[0].outcome == "delivered"
        assert len(notifications) == 2
        assert all(row.status == NotificationStatus.SENT.value for row in notifications)
        stored_sample_leak: bool = any(sample in row.body for row in notifications)
        assert stored_sample_leak is False

        repeated_scan: httpx.Response = await client.post(
            "/api/ingest/v1/scan",
            headers={"Authorization": f"Bearer {ingest.token}"},
            json={
                "source": f"custom:live-alert-{run_id}",
                "resources": [
                    {
                        "locator": f"repo/{run_id}.env",
                        "title": "Live alert delivery verification",
                        "complete": True,
                        "segments": [{"locator": "L1", "text": f"key={sample}"}],
                    }
                ],
            },
        )
        assert repeated_scan.status_code == 200
        await container.worker.run_until_idle()
        async with container.sessions() as session:
            later_fires: tuple[AlertFireRow, ...] = tuple(
                await session.scalars(select(AlertFireRow).where(AlertFireRow.rule_id == rule_id))
            )
            later_notifications: tuple[NotificationRow, ...] = tuple(
                await session.scalars(
                    select(NotificationRow).where(NotificationRow.issue_id == issue_id)
                )
            )
        # D21: a match inside the action interval records one suppressed_interval row and
        # sends nothing, so exactly one delivered fire and the original two notifications.
        assert [fire.outcome for fire in later_fires].count("delivered") == 1
        assert {fire.outcome for fire in later_fires} <= {"delivered", "suppressed_interval"}
        assert len(later_fires) <= 2
        assert len(later_notifications) == 2

        async with httpx.AsyncClient(timeout=15) as mailpit:
            search: httpx.Response = await mailpit.get(
                f"{mailpit_url}/api/v1/search", params={"query": rule_name}
            )
            assert search.status_code == 200
            results: _MailpitSearch = TypeAdapter(_MailpitSearch).validate_json(search.content)
            assert len(results.messages) == 1
            detail: httpx.Response = await mailpit.get(
                f"{mailpit_url}/api/v1/message/{results.messages[0].ID}"
            )
            assert detail.status_code == 200
            assert short_id in detail.text
            leaked_sample: bool = sample in detail.text
            assert leaked_sample is False

        async with httpx.AsyncClient(timeout=15) as slack:
            auth_test: httpx.Response = await slack.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {slack_token}"},
            )
            assert auth_test.status_code == 200 and auth_test.json()["ok"] is True
            scopes: frozenset[str] = frozenset(
                part.strip() for part in auth_test.headers.get("x-oauth-scopes", "").split(",")
            )
            if _HISTORY_SCOPES & scopes:
                history: httpx.Response = await slack.get(
                    "https://slack.com/api/conversations.history",
                    headers={"Authorization": f"Bearer {slack_token}"},
                    params={"channel": slack_channel, "limit": 100},
                )
                assert history.status_code == 200 and history.json()["ok"] is True
                assert rule_name in history.text
    finally:
        if client is not None:
            await client.aclose()
        await close_container(container)
        await database.close()
        await outbound.aclose()
