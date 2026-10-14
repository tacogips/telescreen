"""Production-path field schema, metadata validation, and overdue notification flows."""

from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.db.models import DataAssetEventRow, MemberRow, NotificationRow
from telescreen.notifications.email import SmtplibTransport
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world
from tests.support.samples import aws_access_key_id


@pytest.mark.asyncio
async def test_e5_e6_fields_required_values_audit_and_overdue_notifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validate metadata writes and send one owner notice per changed due date."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder(), independent_sessions=True)
    smtp_messages: list[str] = []

    def capture_smtp(_transport: SmtplibTransport, message: EmailMessage) -> None:
        smtp_messages.append(message.as_string())

    monkeypatch.setattr(SmtplibTransport, "send", capture_smtp)
    try:
        async with world.database.sessions.begin() as session:
            session.add(
                MemberRow(
                    id="v14-owner-id",
                    handle="v14-owner",
                    display_name="V14 Owner",
                    email="owner@example.invalid",
                    slack_user_id=None,
                    active=True,
                    created_at=world.clock.now(),
                    updated_at=world.clock.now(),
                    role="responder",
                )
            )
        registered: httpx.Response = await world.client.post(
            "/api/data/assets",
            headers=bearer(world.admin_token),
            json={
                "name": "Retention sample",
                "source": "manual",
                "locator": "records/retention",
                "kind": "table",
            },
        )
        assert registered.status_code == 201, registered.text
        asset_id: str = registered.json()["id"]
        purpose: httpx.Response = await world.client.post(
            "/api/data/fields",
            headers=bearer(world.admin_token),
            json={
                "key": "purpose",
                "label": "Purpose",
                "field_type": "text",
                "required": True,
                "role": "purpose",
            },
        )
        assert purpose.status_code == 201, purpose.text
        purpose_id: str = purpose.json()["id"]
        detail: httpx.Response = await world.client.get(
            f"/api/data/assets/{asset_id}", headers=bearer(world.admin_token)
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["incomplete"] is True
        incomplete: httpx.Response = await world.client.get(
            "/api/data/assets?tab=incomplete", headers=bearer(world.admin_token)
        )
        assert any(item["id"] == asset_id for item in incomplete.json()["items"])

        secret: str = aws_access_key_id().value
        rejected: httpx.Response = await world.client.put(
            f"/api/data/assets/{asset_id}/fields",
            headers=bearer(world.admin_token),
            json={"values": [{"key": "purpose", "value_json": f'"{secret}"'}]},
        )
        assert rejected.status_code == 422
        assert secret not in rejected.text
        changed: httpx.Response = await world.client.put(
            f"/api/data/assets/{asset_id}/fields",
            headers=bearer(world.admin_token),
            json={"values": [{"key": "purpose", "value_json": '"Operations"'}]},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["incomplete"] is False
        type_change: httpx.Response = await world.client.patch(
            f"/api/data/fields/{purpose_id}",
            headers=bearer(world.admin_token),
            json={"label": "Purpose", "field_type": "integer", "required": True},
        )
        assert type_change.status_code == 409, type_change.text

        owner: httpx.Response = await world.client.post(
            "/api/data/fields",
            headers=bearer(world.admin_token),
            json={
                "key": "owner",
                "label": "Owner",
                "field_type": "reference",
                "role": "owner",
            },
        )
        due: httpx.Response = await world.client.post(
            "/api/data/fields",
            headers=bearer(world.admin_token),
            json={
                "key": "deletion_due",
                "label": "Deletion due",
                "field_type": "date",
                "role": "deletion_due",
            },
        )
        assert owner.status_code == 201, owner.text
        assert due.status_code == 201, due.text

        async def set_retention_date(value: str) -> httpx.Response:
            return await world.client.put(
                f"/api/data/assets/{asset_id}/fields",
                headers=bearer(world.admin_token),
                json={
                    "values": [
                        {"key": "owner", "value_json": '"member:v14-owner"'},
                        {"key": "deletion_due", "value_json": f'"{value}"'},
                    ]
                },
            )

        retention: httpx.Response = await set_retention_date("2026-09-30")
        assert retention.status_code == 200, retention.text
        overdue: httpx.Response = await world.client.get(
            "/api/data/assets?tab=overdue", headers=bearer(world.admin_token)
        )
        assert any(item["id"] == asset_id for item in overdue.json()["items"])

        world.clock.advance(timedelta(hours=9))
        await world.drain()
        async with world.database.sessions() as session:
            first_notices: tuple[NotificationRow, ...] = tuple(
                await session.scalars(
                    select(NotificationRow).where(NotificationRow.event_kind == "data_overdue")
                )
            )
        assert len(first_notices) == 1
        assert smtp_messages
        assert "DATA-" in smtp_messages[0]

        changed_due: httpx.Response = await set_retention_date("2026-09-29")
        assert changed_due.status_code == 200, changed_due.text
        world.clock.advance(timedelta(days=1))
        await world.drain()
        async with world.database.sessions() as session:
            notices: tuple[NotificationRow, ...] = tuple(
                await session.scalars(
                    select(NotificationRow).where(NotificationRow.event_kind == "data_overdue")
                )
            )
            events: tuple[DataAssetEventRow, ...] = tuple(
                await session.scalars(
                    select(DataAssetEventRow).where(
                        DataAssetEventRow.asset_id == asset_id,
                        DataAssetEventRow.kind == "metadata_changed",
                    )
                )
            )
        assert len(notices) == 2
        assert len(events) >= 3
    finally:
        await world.close()
