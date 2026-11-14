"""Inventory metadata commits independently while overdue notification delivery is unavailable."""

from dataclasses import dataclass, replace
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from telescreen.audit.verify import verify_chain
from telescreen.db.models import (
    DataFieldValueRow,
    InventoryRetentionOutboxRow,
    MemberRow,
    NotificationRow,
)
from telescreen.notifications.email import SmtplibTransport
from telescreen.ports.retention import RetentionCommand, RetentionReceipt
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world


@dataclass(frozen=True)
class UnavailableNotifications:
    """Model an unavailable notification receiver while inventory remains usable."""

    async def receive(self, command: RetentionCommand) -> RetentionReceipt:
        """Leave durable inventory obligations pending."""
        del command
        raise RuntimeError("notifications unavailable")


@pytest.mark.asyncio
async def test_due_date_edit_commits_during_notification_outage_and_recovers_latest_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real API edits and worker retries send only the current obligation after recovery."""
    world: E2EWorld = await make_world(
        tmp_path,
        (("TELESCREEN_DATA_OVERDUE_CRON", "* * * * *"),),
        OutboundRecorder(),
        independent_sessions=True,
    )
    messages: list[str] = []

    def capture(_transport: SmtplibTransport, message: EmailMessage) -> None:
        messages.append(message.as_string())

    monkeypatch.setattr(SmtplibTransport, "send", capture)
    try:
        async with world.database.sessions.begin() as session:
            session.add(
                MemberRow(
                    id="owner",
                    handle="alice",
                    display_name="Alice",
                    email="alice@example.invalid",
                    created_at=world.clock.now(),
                    updated_at=world.clock.now(),
                )
            )
        response: httpx.Response = await world.client.post(
            "/api/data/assets",
            headers=bearer(world.admin_token),
            json={
                "name": "Retention",
                "source": "manual",
                "locator": "retention/report",
                "kind": "table",
            },
        )
        assert response.status_code == 201, response.text
        asset_id: str = response.json()["id"]
        for key, kind, role in (
            ("owner", "reference", "owner"),
            ("deletion_due", "date", "deletion_due"),
        ):
            response = await world.client.post(
                "/api/data/fields",
                headers=bearer(world.admin_token),
                json={"key": key, "label": key, "field_type": kind, "role": role},
            )
            assert response.status_code == 201, response.text
        world.container.worker.registry.register(
            replace(
                world.container.v14.retention_delivery_handler, receiver=UnavailableNotifications()
            )
        )
        for due in ("2026-09-30", "2026-09-29"):
            response = await world.client.put(
                f"/api/data/assets/{asset_id}/fields",
                headers=bearer(world.admin_token),
                json={
                    "values": [
                        {"key": "owner", "value_json": '"member:alice"'},
                        {"key": "deletion_due", "value_json": f'"{due}"'},
                    ]
                },
            )
            assert response.status_code == 200, response.text
            world.clock.advance(timedelta(seconds=61))
            assert await world.container.worker.run_until_idle() >= 1
        async with world.database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 0
            pending: tuple[InventoryRetentionOutboxRow, ...] = tuple(
                (await session.scalars(select(InventoryRetentionOutboxRow))).all()
            )
            assert len(pending) == 2 and all(row.delivered_at is None for row in pending)
            assert "2026-09-29" in tuple(
                (await session.scalars(select(DataFieldValueRow.value_date))).all()
            )
        world.container.worker.registry.register(world.container.v14.retention_delivery_handler)
        world.clock.advance(timedelta(minutes=1))
        await world.drain()
        async with world.database.sessions() as session:
            notices: tuple[NotificationRow, ...] = tuple(
                (
                    await session.scalars(
                        select(NotificationRow).where(NotificationRow.event_kind == "data_overdue")
                    )
                ).all()
            )
            assert len(notices) == 1
            assert "2026-09-29" in notices[0].body
            pending = tuple((await session.scalars(select(InventoryRetentionOutboxRow))).all())
            assert all(row.delivered_at is not None for row in pending)
            assert (await verify_chain(session)).ok
        assert len(messages) == 1
    finally:
        await world.close()
