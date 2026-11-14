"""Live v1.3 mention notification delivery through SMTP and Mailpit."""

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
from telescreen.db.models import NotificationRow
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.wiring import Container, build_container, close_container
from tests.live.conftest import require_live_variables
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import github_token


@dataclass(frozen=True)
class _LiveEnv(EnvSource):
    """Resolve local fingerprint and SMTP test settings."""

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


@pytest.mark.live
@pytest.mark.asyncio
async def test_mention_live_delivers_masked_comment_to_mailpit(tmp_path: Path) -> None:
    """Notify a member by email when a comment mentions their configured handle."""
    require_live_variables(
        "TELESCREEN_TEST_SMTP_HOST",
        "TELESCREEN_TEST_SMTP_PORT",
        "TELESCREEN_TEST_MAILPIT_URL",
        "TELESCREEN_TEST_EMAIL_TO",
    )
    smtp_host: str = os.environ["TELESCREEN_TEST_SMTP_HOST"]
    smtp_port: str = os.environ["TELESCREEN_TEST_SMTP_PORT"]
    mailpit_url: str = os.environ["TELESCREEN_TEST_MAILPIT_URL"].rstrip("/")
    recipient: str = os.environ["TELESCREEN_TEST_EMAIL_TO"]
    run_id: str = uuid4().hex
    handle: str = f"live-mention-{run_id}"
    sample: str = github_token().value
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
                "live-mention-admin",
                frozenset((ApiScope.ADMIN, ApiScope.READ, ApiScope.WRITE)),
                None,
            )
            ingest: CreatedToken = await tokens.create(
                session, "live-mention-ingest", frozenset((ApiScope.INGEST,)), None
            )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(container)),
            base_url="http://telescreen.test",
        )
        member: httpx.Response = await client.post(
            "/api/members",
            headers={"Authorization": f"Bearer {admin.token}"},
            json={"handle": handle, "display_name": "Live Mention", "email": recipient},
        )
        assert member.status_code == 201
        # A directory member has no role; a bound token gets role & scope permissions.
        role: httpx.Response = await client.put(
            f"/api/users/{handle}/role",
            headers={"Authorization": f"Bearer {admin.token}"},
            json={"role": "viewer"},
        )
        assert role.status_code == 200
        member_token_response: httpx.Response = await client.post(
            "/api/tokens",
            headers={"Authorization": f"Bearer {admin.token}"},
            json={"label": f"live-mention-{run_id}", "scopes": ["read"], "member_handle": handle},
        )
        assert member_token_response.status_code == 201
        member_token: str = member_token_response.json()["token"]
        preference: httpx.Response = await client.put(
            "/api/auth/me/notification-settings",
            headers={"Authorization": f"Bearer {member_token}"},
            json={"items": [{"category": "mentions", "stored": "email"}]},
        )
        assert preference.status_code == 200
        collector: httpx.Response = await client.post(
            "/api/collectors",
            headers={"Authorization": f"Bearer {admin.token}"},
            json={
                "name": f"live-mention-{run_id}",
                "kind": "custom",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": '{"paths":["**"]}',
                "credential_ref": None,
                "enabled": True,
            },
        )
        assert collector.status_code == 200
        scan: httpx.Response = await client.post(
            "/api/ingest/v1/scan",
            headers={"Authorization": f"Bearer {ingest.token}"},
            json={
                "source": f"custom:live-mention-{run_id}",
                "resources": [
                    {
                        "locator": f"repo/{run_id}.env",
                        "title": "Live mention delivery verification",
                        "complete": True,
                        "segments": [{"locator": "L1", "text": f"token={sample}"}],
                    }
                ],
            },
        )
        assert scan.status_code == 200
        issue_ids: list[str] = scan.json()["issue_ids"]
        assert issue_ids
        issue_id: str = issue_ids[0]
        issue_response: httpx.Response = await client.get(
            f"/api/issues/{issue_id}",
            headers={"Authorization": f"Bearer {admin.token}"},
        )
        assert issue_response.status_code == 200
        short_id: str = issue_response.json()["short_id"]
        comment: httpx.Response = await client.post(
            f"/api/issues/{issue_id}/comments",
            headers={"Authorization": f"Bearer {admin.token}"},
            json={"body": f"Live verification {run_id}: please review this finding, @{handle}"},
        )
        assert comment.status_code == 201
        await container.worker.run_until_idle()
        async with container.sessions() as session:
            notifications: tuple[NotificationRow, ...] = tuple(
                await session.scalars(
                    select(NotificationRow).where(NotificationRow.issue_id == issue_id)
                )
            )
        assert len(notifications) == 1
        assert notifications[0].status == NotificationStatus.SENT.value
        stored_sample_leak: bool = any(sample in row.body for row in notifications)
        assert stored_sample_leak is False

        async with httpx.AsyncClient(timeout=15) as mailpit:
            search: httpx.Response = await mailpit.get(
                f"{mailpit_url}/api/v1/search", params={"query": run_id}
            )
            assert search.status_code == 200
            results: _MailpitSearch = TypeAdapter(_MailpitSearch).validate_json(search.content)
            assert len(results.messages) == 1
            detail: httpx.Response = await mailpit.get(
                f"{mailpit_url}/api/v1/message/{results.messages[0].ID}"
            )
            assert detail.status_code == 200
            assert short_id in detail.text
            received_sample_leak: bool = sample in detail.text
            assert received_sample_leak is False
    finally:
        if client is not None:
            await client.aclose()
        await close_container(container)
        await database.close()
        await outbound.aclose()
