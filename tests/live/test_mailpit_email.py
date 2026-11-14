"""Live Mailpit delivery through the production notification service."""

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import TypeAdapter
from sqlalchemy import select

from telescreen.core.vocabulary import NotificationChannel, NotificationEvent, NotificationStatus
from telescreen.db.models import NotificationRow
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from tests.e2e.harness import E2EWorld, OutboundRecorder, make_world
from tests.live.conftest import require_live_variables
from tests.support.samples import github_token


@dataclass(frozen=True)
class _MailpitMessage:
    """Mailpit search result fields consumed by the test."""

    ID: str


@dataclass(frozen=True)
class _MailpitSearch:
    """Typed Mailpit search response."""

    messages: list[_MailpitMessage]


@pytest.mark.live
@pytest.mark.asyncio
async def test_mailpit_receives_masked_email_via_real_delivery(tmp_path: Path) -> None:
    """Send masked content through SMTP and confirm Mailpit exposes the message."""
    require_live_variables(
        "TELESCREEN_TEST_MAILPIT_URL", "TELESCREEN_TEST_SMTP_HOST", "TELESCREEN_TEST_SMTP_PORT"
    )
    host: str = os.environ["TELESCREEN_TEST_SMTP_HOST"]
    port: int = int(os.environ["TELESCREEN_TEST_SMTP_PORT"])
    mailpit_url: str = os.environ["TELESCREEN_TEST_MAILPIT_URL"].rstrip("/")
    recipient: str = os.environ.get("TELESCREEN_TEST_EMAIL_TO", "telescreen@example.test")
    run_token: str = uuid4().hex
    sample: str = github_token().value
    world: E2EWorld = await make_world(
        tmp_path,
        (
            ("TELESCREEN_SMTP_HOST", host),
            ("TELESCREEN_SMTP_PORT", str(port)),
            ("TELESCREEN_SMTP_STARTTLS", "false"),
            ("TELESCREEN_SMTP_FROM", "telescreen@example.test"),
        ),
        OutboundRecorder(),
    )
    try:
        created: httpx.Response = await world.client.post(
            "/api/notification-templates",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={
                "name": "live.mailpit",
                "event_kind": "workflow_notify",
                "subject": f"Telescreen live {run_token}",
                "body": "Template delivery verification",
            },
        )
        assert created.status_code == 201, created.text
        activated: httpx.Response = await world.client.post(
            "/api/notification-templates/live.mailpit/activate",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"version": 1},
        )
        assert activated.status_code == 200, activated.text
        async with world.container.sessions.begin() as session:
            template_receipt: NotificationReceipt = await world.container.v12.notifications.send(
                session,
                NotificationRequest(
                    NotificationChannel.EMAIL,
                    (f"email:{recipient}",),
                    "",
                    "",
                    "system:live-test",
                    event=NotificationEvent.WORKFLOW_NOTIFY,
                    template_name="live.mailpit",
                ),
            )
            inline_receipt: NotificationReceipt = await world.container.v12.notifications.send(
                session,
                NotificationRequest(
                    NotificationChannel.EMAIL,
                    (f"email:{recipient}",),
                    f"Telescreen live {run_token}",
                    sample,
                    "system:live-test",
                    event=NotificationEvent.WORKFLOW_NOTIFY,
                    inline=True,
                ),
            )
        assert template_receipt.notification_ids and inline_receipt.notification_ids
        await world.drain()
        async with world.container.sessions() as session:
            rows: tuple[NotificationRow, ...] = tuple(
                await session.scalars(
                    select(NotificationRow).where(
                        NotificationRow.id.in_(
                            template_receipt.notification_ids + inline_receipt.notification_ids
                        )
                    )
                )
            )
        assert len(rows) == 2
        assert all(row.status == NotificationStatus.SENT.value for row in rows)
        assert all(sample not in row.body for row in rows)
    finally:
        await world.close()
    async with httpx.AsyncClient(timeout=15) as client:
        search: httpx.Response = await client.get(
            f"{mailpit_url}/api/v1/search", params={"query": run_token}
        )
        assert search.status_code == 200
        results: _MailpitSearch = TypeAdapter(_MailpitSearch).validate_json(search.content)
        assert results.messages
        assert len(results.messages) >= 2
        for message in results.messages:
            detail: httpx.Response = await client.get(f"{mailpit_url}/api/v1/message/{message.ID}")
            assert detail.status_code == 200
            assert run_token in detail.text
            assert sample not in detail.text
