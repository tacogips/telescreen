"""Live Slack delivery verification with scope-aware optional history read-back."""

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.config import TelescreenSettings
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import NotificationChannel, NotificationEvent, NotificationStatus
from telescreen.db.models import NotificationRow
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.wiring import Container, build_container, close_container
from tests.live.conftest import require_live_variables
from tests.support.db import SqliteHarness, make_sqlite_harness

_HISTORY_SCOPES: frozenset[str] = frozenset(
    ("channels:history", "groups:history", "im:history", "mpim:history")
)


@pytest.mark.live
@pytest.mark.asyncio
async def test_slack_delivery_posts_and_reads_back_only_when_scoped(tmp_path: Path) -> None:
    """Post through SlackNotifier and use history only when token scopes permit it."""
    require_live_variables("TELESCREEN_TEST_SLACK_BOT_TOKEN", "TELESCREEN_TEST_SLACK_CHANNEL")
    token: str = os.environ["TELESCREEN_TEST_SLACK_BOT_TOKEN"]
    channel: str = os.environ["TELESCREEN_TEST_SLACK_CHANNEL"]
    run_token: str = uuid4().hex
    database: SqliteHarness = await make_sqlite_harness()
    env: EnvSource = _SlackEnv(token)
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
        slack_bot_token_ref="env:TELESCREEN_TEST_SLACK_TOKEN",
        evidence_dir=str(tmp_path / "evidence"),
    )
    outbound: httpx.AsyncClient = httpx.AsyncClient(timeout=20)
    container: Container = build_container(settings, env, outbound, database.sessions)
    try:
        async with container.sessions.begin() as session:
            receipt: NotificationReceipt = await container.v12.notifications.send(
                session,
                NotificationRequest(
                    NotificationChannel.SLACK,
                    (f"slack_channel:{channel}",),
                    f"Telescreen live {run_token}",
                    "Live delivery verification",
                    "system:live-test",
                    event=NotificationEvent.WORKFLOW_NOTIFY,
                    inline=True,
                ),
            )
        assert receipt.notification_ids
        await container.worker.run_until_idle()
        async with container.sessions() as session:
            row: NotificationRow | None = await session.scalar(
                select(NotificationRow).where(NotificationRow.id == receipt.notification_ids[0])
            )
        assert row is not None and row.status == NotificationStatus.SENT.value
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()

    async with httpx.AsyncClient(timeout=15) as client:
        auth_test: httpx.Response = await client.post(
            "https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {token}"}
        )
        assert auth_test.status_code == 200 and auth_test.json()["ok"] is True
        scopes: frozenset[str] = frozenset(
            part.strip() for part in auth_test.headers.get("x-oauth-scopes", "").split(",")
        )
        if _HISTORY_SCOPES & scopes:
            history: httpx.Response = await client.get(
                "https://slack.com/api/conversations.history",
                headers={"Authorization": f"Bearer {token}"},
                params={"channel": channel, "limit": 20},
            )
            assert history.status_code == 200 and history.json()["ok"] is True
            assert run_token in history.text


@dataclass(frozen=True)
class _SlackEnv(EnvSource):
    """Resolve the live test's local fingerprint and Slack token references."""

    token: str

    def get(self, key: str, /) -> str | None:
        """Return configured secrets without exposing the value in representations."""
        return {
            "TELESCREEN_TEST_KEY": "live-test-fingerprint-key-000000000000",
            "TELESCREEN_TEST_SLACK_TOKEN": self.token,
        }.get(key)
