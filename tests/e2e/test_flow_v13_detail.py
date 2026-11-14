"""End-to-end v1.3 issue detail navigation and member activity flow."""

from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import MemberRow, NotificationRow
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.notifications.email import SmtplibTransport
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world
from tests.support.samples import aws_access_key_id


@pytest.mark.asyncio
async def test_e3_detail_events_tags_mentions_and_subscriber_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Navigate 120 events and deliver a mention without notifying the actor."""
    captured_email: list[str] = []

    def capture_email(_transport: SmtplibTransport, message: EmailMessage) -> None:
        body: str = message.get_content()
        subject: str = message.get("Subject", "")
        captured_email.append(f"{subject}\n{body}")

    monkeypatch.setattr(SmtplibTransport, "send", capture_email)
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    try:
        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "v13-e3",
                "kind": "custom",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": "{}",
                "credential_ref": None,
                "enabled": True,
            },
        )
        assert collector.status_code in (200, 201), collector.text
        async with world.database.sessions.begin() as session:
            ingest_token: CreatedToken = await TokenService(
                world.container.audit, world.clock
            ).create(session, "v13-e3-ingest", frozenset((ApiScope.INGEST,)), None)
        sample: str = aws_access_key_id().value

        async def ingest() -> str:
            response: httpx.Response = await world.client.post(
                "/api/ingest/v1/scan",
                headers=bearer(ingest_token.token),
                json={
                    "source": "custom:v13-e3",
                    "resources": [
                        {"locator": "detail.py", "segments": [{"locator": "L1", "text": sample}]}
                    ],
                },
            )
            assert response.status_code == 200, response.text
            issue_ids: list[str] = response.json()["issue_ids"]
            assert len(issue_ids) == 1
            return issue_ids[0]

        issue_id: str = await ingest()
        for _ in range(119):
            world.clock.advance(timedelta(seconds=1))
            assert await ingest() == issue_id

        oldest: httpx.Response = await world.client.get(
            f"/api/issues/{issue_id}/events/oldest", headers=bearer(world.admin_token)
        )
        latest: httpx.Response = await world.client.get(
            f"/api/issues/{issue_id}/events/latest", headers=bearer(world.admin_token)
        )
        assert oldest.status_code == latest.status_code == 200
        assert oldest.json()["position"] == 1 and oldest.json()["total"] == 120
        assert latest.json()["position"] == latest.json()["total"] == 120
        assert oldest.json()["next_id"] is not None
        assert latest.json()["previous_id"] is not None
        world.clock.advance(timedelta(seconds=1))
        tags: httpx.Response = await world.client.get(
            f"/api/issues/{issue_id}/tags", headers=bearer(world.admin_token)
        )
        stats: httpx.Response = await world.client.get(
            f"/api/issues/{issue_id}/stats?period=14d", headers=bearer(world.admin_token)
        )
        assert tags.status_code == stats.status_code == 200
        assert tags.json()["total"] == 120
        assert stats.json()["total"] == 120

        async def create_member(handle: str, email: str) -> tuple[str, str]:
            created: httpx.Response = await world.client.post(
                "/api/members",
                headers=bearer(world.admin_token),
                json={
                    "handle": handle,
                    "display_name": handle.title(),
                    "email": email,
                    "role": "responder",
                },
            )
            assert created.status_code == 201, created.text
            role: httpx.Response = await world.client.put(
                f"/api/users/{handle}/role",
                headers=bearer(world.admin_token),
                json={"role": "responder"},
            )
            assert role.status_code == 200, role.text
            async with world.database.sessions.begin() as session:
                row: MemberRow | None = await session.scalar(
                    select(MemberRow).where(MemberRow.handle == handle)
                )
                assert row is not None
                token: CreatedToken = await TokenService(world.container.audit, world.clock).create(
                    session,
                    f"v13-e3-{handle}",
                    frozenset((ApiScope.READ, ApiScope.WRITE)),
                    row.id,
                )
                member_id: str = row.id
            return member_id, token.token

        actor_id: str
        actor_token: str
        actor_id, actor_token = await create_member("e3-actor", "actor@example.test")
        target_id: str
        target_token: str
        target_id, target_token = await create_member("e3-target", "target@example.test")
        for token in (actor_token, target_token):
            settings: httpx.Response = await world.client.put(
                "/api/auth/me/notification-settings",
                headers=bearer(token),
                json={
                    "items": [
                        {"category": "mentions", "stored": "email"},
                        {"category": "subscribed", "stored": "email"},
                    ]
                },
            )
            assert settings.status_code == 200, settings.text
        subscribed: httpx.Response = await world.client.put(
            f"/api/issues/{issue_id}/subscription",
            headers=bearer(actor_token),
            json={"subscribed": True},
        )
        assert subscribed.status_code == 200, subscribed.text
        comment: httpx.Response = await world.client.post(
            f"/api/issues/{issue_id}/comments",
            headers=bearer(actor_token),
            json={"body": "Review this finding, @e3-target"},
        )
        assert comment.status_code == 201, comment.text
        await world.drain()
        async with world.database.sessions() as session:
            notifications: tuple[NotificationRow, ...] = tuple(
                await session.scalars(
                    select(NotificationRow).where(NotificationRow.issue_id == issue_id)
                )
            )
        target_deliveries: tuple[NotificationRow, ...] = tuple(
            row for row in notifications if row.recipient_ref == "member:e3-target"
        )
        actor_deliveries: tuple[NotificationRow, ...] = tuple(
            row for row in notifications if row.recipient_ref == "member:e3-actor"
        )
        # The mention auto-subscribes the target; the same comment must not notify it twice.
        assert [row.category for row in target_deliveries] == ["mentions"]
        assert actor_deliveries == ()
        assert captured_email
        assert any("TELE-" in body for body in captured_email)
        assert actor_id != target_id
    finally:
        await world.close()
