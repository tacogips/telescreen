"""The assembled API and worker use physically separate main and inventory tables."""

from datetime import timedelta
from email.message import EmailMessage

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from telescreen.audit.verify import verify_chain
from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import (
    CounterRow,
    DataAssetRow,
    DataFieldValueRow,
    InventoryAuditOutboxRow,
    InventoryCounterRow,
    InventoryRetentionOutboxRow,
    IssueRow,
    MemberRow,
    NotificationRow,
)
from telescreen.directory.tokens import CreatedToken
from telescreen.notifications.email import SmtplibTransport
from tests.e2e.harness import E2EWorld, bearer
from tests.support.db import SqliteHarness
from tests.support.samples import aws_access_key_id


async def assert_split_runtime(world: E2EWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise API, worker and both handoffs using the supplied independently owned stores."""
    inventory: SqliteHarness | None = world.inventory_database
    assert inventory is not None
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
            counter: CounterRow | None = await session.get(CounterRow, "issue_number")
            assert counter is not None
            counter.value = 40
            ingest: CreatedToken = await world.container.token_service.create(
                session, "split-ingest", frozenset((ApiScope.INGEST,)), None
            )
        for key, kind, role in (
            ("owner", "reference", "owner"),
            ("deletion_due", "date", "deletion_due"),
        ):
            response: httpx.Response = await world.client.post(
                "/api/data/fields",
                headers=bearer(world.admin_token),
                json={"key": key, "label": key, "field_type": kind, "role": role},
            )
            assert response.status_code == 201, response.text
        response = await world.client.post(
            "/api/data/assets",
            headers=bearer(world.admin_token),
            json={
                "name": "Master register",
                "source": "manual",
                "locator": "records/master",
                "kind": "table",
                "values": [
                    {"key": "owner", "value_json": '"member:alice"'},
                    {"key": "deletion_due", "value_json": '"2026-09-30"'},
                ],
            },
        )
        assert response.status_code == 201, response.text
        master_id: str = response.json()["id"]
        response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "split",
                "kind": "custom",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "credential_ref": None,
                "selector_json": "{}",
                "inventory_mode": "all",
                "enabled": True,
            },
        )
        assert response.status_code == 200, response.text
        response = await world.client.post(
            "/api/ingest/v1/scan",
            headers=bearer(ingest.token),
            json={
                "source": "custom:split",
                "resources": [
                    {
                        "locator": "safe.csv",
                        "complete": True,
                        "removed": False,
                        "segments": [{"locator": "line:1", "text": aws_access_key_id().value}],
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        world.clock.advance(timedelta(seconds=61))
        await world.drain()
        async with inventory.sessions() as session:
            assets: tuple[DataAssetRow, ...] = tuple(
                (await session.scalars(select(DataAssetRow).order_by(DataAssetRow.number))).all()
            )
            assert tuple(asset.number for asset in assets) == (1, 2)
            assert assets[0].id == master_id and assets[0].name == "Master register"
            discovered_id: str = assets[1].id
            assert await session.scalar(select(InventoryCounterRow.value)) == 2
            assert await session.scalar(select(func.count()).select_from(DataFieldValueRow)) == 2
            assert not tuple(
                (
                    await session.scalars(
                        select(InventoryAuditOutboxRow).where(
                            InventoryAuditOutboxRow.delivered_at.is_(None)
                        )
                    )
                ).all()
            )
            assert not tuple(
                (
                    await session.scalars(
                        select(InventoryRetentionOutboxRow).where(
                            InventoryRetentionOutboxRow.delivered_at.is_(None)
                        )
                    )
                ).all()
            )
        for path in (
            "/api/data/assets",
            "/api/data/counts",
            "/api/data/fields",
            "/api/data/export",
            f"/api/data/assets/{master_id}",
            f"/api/data/assets/{master_id}/events",
            f"/api/data/assets/{master_id}/items",
            f"/api/data/assets/{master_id}/item-series",
            f"/api/data/assets/{discovered_id}",
        ):
            response = await world.client.get(path, headers=bearer(world.admin_token))
            assert response.status_code == 200, (path, response.text)
        detail: httpx.Response = await world.client.get(
            f"/api/data/assets/{discovered_id}", headers=bearer(world.admin_token)
        )
        assert len(detail.json()["open_issues"]) == 1
        await world.drain()
        async with world.database.sessions() as session:
            assert await session.scalar(select(IssueRow.number)) == 41
            assert (
                await session.scalar(
                    select(CounterRow.value).where(CounterRow.name == "issue_number")
                )
                == 41
            )
            assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 1
            assert (await verify_chain(session)).ok
        assert len(messages) == 1
        async with world.database.sessions() as session:
            with pytest.raises(DBAPIError, match="no such table|does not exist|permission denied"):
                await session.scalar(select(DataAssetRow.id))
        async with inventory.sessions() as session:
            with pytest.raises(DBAPIError, match="no such table|does not exist|permission denied"):
                await session.scalar(select(MemberRow.id))
    finally:
        await world.close()
