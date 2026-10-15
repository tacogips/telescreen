"""Real pending work survives shared -> split -> shared store cutover and new writes."""

import asyncio
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest
from alembic import command
from sqlalchemy import func, select

from telescreen.audit.verify import verify_chain
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind
from telescreen.db.adopt import adopt_owner
from telescreen.db.migrate import owner_config, upgrade_store
from telescreen.db.models import (
    AuditReceiptRow,
    DataAssetRow,
    DataFieldValueRow,
    InventoryAuditOutboxRow,
    InventoryInboxRow,
    InventoryOutboxRow,
    InventoryRetentionOutboxRow,
    MemberRow,
    NotificationRow,
    RetentionNotificationReceiptRow,
)
from telescreen.db.ownership import Store
from telescreen.db.transfer import transfer_inventory
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.notifications.email import SmtplibTransport
from telescreen.ports.inventory_commands import InventoryCommand
from telescreen.ports.workflow import ClaimedTask
from telescreen.wiring_v14 import WiringV14
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world
from tests.inventory.handoff.test_delivery import observed
from tests.support.owned_postgres import OwnedPostgres, owned_postgres

__all__: list[str] = ["owned_postgres"]


async def cutover_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, main_url: str, inventory_url: str
) -> None:
    """Run production APIs, receivers, migrations and worker across two stopped-writer switches."""
    messages: list[str] = []

    def capture(_transport: SmtplibTransport, message: EmailMessage) -> None:
        messages.append(message.as_string())

    monkeypatch.setattr(SmtplibTransport, "send", capture)
    old: E2EWorld = await make_world(
        tmp_path, (), OutboundRecorder(), store_urls=(main_url, main_url)
    )
    master_id: str
    audit_ids: tuple[str, ...]
    retention_ids: tuple[str, ...]
    original_revision: str
    first: InventoryCommand
    second: InventoryCommand
    try:
        async with old.database.sessions.begin() as session:
            session.add(
                MemberRow(
                    id="owner",
                    handle="alice",
                    display_name="Alice",
                    email="alice@example.invalid",
                    created_at=old.clock.now(),
                    updated_at=old.clock.now(),
                )
            )
        for key, kind, role in (
            ("owner", "reference", "owner"),
            ("deletion_due", "date", "deletion_due"),
        ):
            response: httpx.Response = await old.client.post(
                "/api/data/fields",
                headers=bearer(old.admin_token),
                json={"key": key, "label": key, "field_type": kind, "role": role},
            )
            assert response.status_code == 201, response.text
        response = await old.client.post(
            "/api/data/assets",
            headers=bearer(old.admin_token),
            json={
                "name": "Original master",
                "source": "manual",
                "locator": "master/original",
                "kind": "table",
                "values": [
                    {"key": "owner", "value_json": '"member:alice"'},
                    {"key": "deletion_due", "value_json": '"2026-09-30"'},
                ],
            },
        )
        assert response.status_code == 201, response.text
        master_id = response.json()["id"]
        assert isinstance(old.container.v14, WiringV14)
        await old.container.v14.overdue_handler.handle(
            ClaimedTask("check", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "due")
        )
        outbox: InventoryOutbox = InventoryOutbox()
        async with old.database.sessions.begin() as session:
            first = await outbox.append(
                session, "collector:cutover", old.clock.now(), observed(old.clock.now())
            )
            second = await outbox.append(
                session, "collector:cutover", old.clock.now(), observed(old.clock.now(), "b" * 64)
            )
        # Commit the first projection but lose its producer acknowledgment before cutover.
        await old.container.v14.inbox.receive(first)
        async with old.database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(InventoryInboxRow)) == 1
            assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 0
            audit_ids = tuple((await session.scalars(select(InventoryAuditOutboxRow.id))).all())
            retention_ids = tuple(
                (await session.scalars(select(InventoryRetentionOutboxRow.id))).all()
            )
            original_revision = (
                await session.scalars(
                    select(DataFieldValueRow.revision_id).where(
                        DataFieldValueRow.value_date.is_not(None)
                    )
                )
            ).one()
            assert audit_ids and len(retention_ids) == 1
    finally:
        await old.close()
    await asyncio.to_thread(adopt_owner, main_url, Store.MAIN, writers_stopped=True)
    await asyncio.to_thread(upgrade_store, inventory_url, Store.INVENTORY)
    assert (await transfer_inventory(main_url, inventory_url, writers_stopped=True)).copied
    await transfer_inventory(main_url, inventory_url, writers_stopped=True, verify_only=True)

    split: E2EWorld = await make_world(
        tmp_path, (), OutboundRecorder(), store_urls=(main_url, inventory_url)
    )
    new_id: str
    changed_revision: str
    try:
        split.clock.advance(timedelta(seconds=65))
        await split.drain()
        async with split.container.inventory_sessions() as session:
            assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 3
            assert tuple(
                (
                    await session.scalars(
                        select(InventoryInboxRow.id).order_by(InventoryInboxRow.sequence)
                    )
                ).all()
            ) == (first.event_id, second.event_id)
            assert (
                await session.scalar(
                    select(DataFieldValueRow.revision_id).where(
                        DataFieldValueRow.value_date.is_not(None)
                    )
                )
                == original_revision
            )
        response = await split.client.post(
            "/api/data/assets",
            headers=bearer(split.admin_token),
            json={
                "name": "Created after split",
                "source": "manual",
                "locator": "master/new",
                "kind": "table",
            },
        )
        assert response.status_code == 201, response.text
        new_id = response.json()["id"]
        response = await split.client.put(
            f"/api/data/assets/{master_id}/fields",
            headers=bearer(split.admin_token),
            json={"values": [{"key": "deletion_due", "value_json": '"2026-10-02"'}]},
        )
        assert response.status_code == 200, response.text
        await split.drain()
        async with split.container.inventory_sessions() as session:
            changed_revision = (
                await session.scalars(
                    select(DataFieldValueRow.revision_id).where(
                        DataFieldValueRow.value_date.is_not(None)
                    )
                )
            ).one()
            assert changed_revision != original_revision
            assert (
                await session.scalar(select(DataAssetRow.number).where(DataAssetRow.id == new_id))
                == 4
            )
            assert not tuple(
                (
                    await session.scalars(
                        select(InventoryAuditOutboxRow.id).where(
                            InventoryAuditOutboxRow.delivered_at.is_(None)
                        )
                    )
                ).all()
            )
        async with split.database.sessions() as session:
            assert set((await session.scalars(select(AuditReceiptRow.id))).all()) >= set(audit_ids)
            assert set(
                (await session.scalars(select(RetentionNotificationReceiptRow.id))).all()
            ) == set(retention_ids)
            assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 1
            assert not tuple(
                (
                    await session.scalars(
                        select(InventoryOutboxRow.id).where(
                            InventoryOutboxRow.delivered_at.is_(None)
                        )
                    )
                ).all()
            )
            # The old local projection remains unchanged until the explicit rollback copy.
            assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 2
    finally:
        await split.close()
    assert len(messages) == 1
    assert (
        await transfer_inventory(inventory_url, main_url, writers_stopped=True, replace=True)
    ).copied
    await transfer_inventory(inventory_url, main_url, writers_stopped=True, verify_only=True)
    await asyncio.to_thread(adopt_owner, main_url, Store.INVENTORY, writers_stopped=True)

    restored: E2EWorld = await make_world(
        tmp_path, (), OutboundRecorder(), store_urls=(main_url, main_url)
    )
    try:
        async with restored.database.sessions() as session:
            assert (
                await session.scalar(select(DataAssetRow.number).where(DataAssetRow.id == new_id))
                == 4
            )
            assert (
                await session.scalar(
                    select(DataFieldValueRow.revision_id).where(
                        DataFieldValueRow.value_date.is_not(None)
                    )
                )
                == changed_revision
            )
        response = await restored.client.post(
            "/api/data/assets",
            headers=bearer(restored.admin_token),
            json={
                "name": "After rollback",
                "source": "manual",
                "locator": "master/restored",
                "kind": "table",
            },
        )
        assert response.status_code == 201, response.text
        async with restored.database.sessions() as session:
            assert (
                await session.scalar(
                    select(DataAssetRow.number).where(DataAssetRow.id == response.json()["id"])
                )
                == 5
            )
        restored.clock.advance(timedelta(seconds=65))
        await restored.drain()
        async with restored.database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 1
            assert (await verify_chain(session)).ok
    finally:
        await restored.close()
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_sqlite_real_pending_handoffs_and_new_writes_survive_cutover_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use file-backed SQLite and the real application at every stage."""
    await cutover_flow(
        tmp_path,
        monkeypatch,
        f"sqlite+aiosqlite:///{tmp_path / 'main.db'}",
        f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}",
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pg_real_pending_handoffs_and_new_writes_survive_cutover_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owned_postgres: OwnedPostgres
) -> None:
    """Use independently owned PostgreSQL schemas and migration/runtime login credentials."""
    await asyncio.to_thread(
        command.downgrade, owner_config(owned_postgres.main_url, Store.MAIN), "base"
    )
    await cutover_flow(tmp_path, monkeypatch, owned_postgres.main_url, owned_postgres.inventory_url)
