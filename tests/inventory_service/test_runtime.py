"""Service boundary behavior using real API handlers and disjoint persistence."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from telescreen.api.common.auth import hash_token
from telescreen.audit.verify import verify_chain
from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import (
    ApiTokenRow,
    DataAssetRow,
    InventoryAuditOutboxRow,
    InventoryRetentionOutboxRow,
    IssueRow,
    MemberRow,
    NotificationRow,
)
from telescreen.directory.tokens import CreatedToken
from telescreen.inventory_service.transport import PREFIX
from telescreen.inventory_service.wiring import RemoteInventoryWiring
from tests.e2e.harness import bearer
from tests.inventory_service.conftest import RemoteWorld
from tests.support.samples import aws_access_key_id


async def test_public_api_authentication_and_independent_ownership(
    remote_world: RemoteWorld,
) -> None:
    """Inventory APIs require service plus user authentication and preserve permissions."""
    world: RemoteWorld = remote_world
    assert isinstance(world.container.v14, RemoteInventoryWiring)
    assert world.container.inventory_engine is None
    assert world.container.inventory_sessions is world.container.sessions
    for path in ("/api/data/assets", PREFIX + "/health", PREFIX + "/commands"):
        response: httpx.Response = await world.client.get("http://inventory" + path)
        assert response.status_code == 401
    response = await world.client.post(PREFIX + "/authenticate", headers=bearer(world.admin))
    assert response.status_code == 401
    response = await world.client.get("/api/data/assets")
    assert response.status_code == 401
    response = await world.client.post(
        "/api/data/fields",
        headers=bearer(world.viewer),
        json={"key": "purpose", "label": "Purpose", "field_type": "text"},
    )
    assert response.status_code == 403
    response = await world.client.post(
        "/api/data/assets",
        headers=bearer(world.admin),
        json={"name": "Service master", "source": "manual", "locator": "master/1", "kind": "table"},
    )
    assert response.status_code == 201, response.text
    asset_id: str = response.json()["id"]
    response = await world.client.get(f"/api/data/assets/{asset_id}", headers=bearer(world.viewer))
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Service master"
    response = await world.client.get("/api/data/export", headers=bearer(world.admin))
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "content-disposition" in response.headers
    async with world.main.sessions() as session:
        with pytest.raises(DBAPIError):
            await session.scalar(select(DataAssetRow.id))
    async with world.inventory.sessions() as session:
        with pytest.raises(DBAPIError):
            await session.scalar(select(IssueRow.id))
    async with world.main.sessions.begin() as session:
        token: ApiTokenRow | None = await session.scalar(
            select(ApiTokenRow).where(ApiTokenRow.token_hash == hash_token(world.admin))
        )
        assert token is not None
        token.revoked_at = world.clock.now()
    response = await world.client.get("/api/data/assets", headers=bearer(world.admin))
    assert response.status_code == 401


async def test_collection_outage_then_lost_reply_replays_once(remote_world: RemoteWorld) -> None:
    """Issue ingestion commits while inventory is down; inbox replay creates one master."""
    world: RemoteWorld = remote_world
    ingest: CreatedToken
    async with world.main.sessions.begin() as session:
        ingest = await world.container.token_service.create(
            session, "ingest", frozenset((ApiScope.INGEST,)), None
        )
    response: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=bearer(world.admin),
        json={
            "name": "remote",
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
    world.network.blocked.add("inventory")
    response = await world.client.post(
        "/api/ingest/v1/scan",
        headers=bearer(ingest.token),
        json={
            "source": "custom:remote",
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
    await world.container.worker.run_until_idle()
    async with world.main.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IssueRow)) == 1
    async with world.inventory.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 0
    response = await world.client.get("/api/data/assets", headers=bearer(world.admin))
    assert response.status_code == 503
    world.network.blocked.clear()
    world.network.lose_reply.add(PREFIX + "/commands")
    for _ in range(3):
        world.clock.advance(timedelta(seconds=61))
        await world.container.worker.run_until_idle()
    async with world.inventory.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 1
    async with world.main.sessions() as session:
        assert await world.container.v14.publisher.outbox.pending(session, 100) == ()
    response = await world.client.get("/api/data/assets", headers=bearer(world.admin))
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["open_issue_count"] == 1


async def test_audit_and_retention_survive_callback_outage(remote_world: RemoteWorld) -> None:
    """Inventory commits during bureau outage and retries lost acknowledgments once."""
    world: RemoteWorld = remote_world
    async with world.main.sessions.begin() as session:
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
    for key, kind, role in (("owner", "reference", "owner"), ("due", "date", "deletion_due")):
        response: httpx.Response = await world.client.post(
            "/api/data/fields",
            headers=bearer(world.admin),
            json={"key": key, "label": key, "field_type": kind, "role": role},
        )
        assert response.status_code == 201, response.text
    response = await world.client.post(
        "/api/data/assets",
        headers=bearer(world.admin),
        json={
            "name": "Due master",
            "source": "manual",
            "locator": "due/1",
            "kind": "table",
            "values": [
                {"key": "owner", "value_json": '"member:alice"'},
                {"key": "due", "value_json": '"2026-09-30"'},
            ],
        },
    )
    assert response.status_code == 201, response.text
    world.network.blocked.add("bureau")
    await world.runtime.worker.publish_due()
    await world.runtime.worker.audit_tick()
    await world.runtime.worker.retention_tick()
    async with world.inventory.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(InventoryAuditOutboxRow)
                .where(InventoryAuditOutboxRow.delivered_at.is_(None))
            )
            or 0
        ) > 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(InventoryRetentionOutboxRow)
                .where(InventoryRetentionOutboxRow.delivered_at.is_(None))
            )
            == 1
        )
    world.network.blocked.clear()
    world.network.lose_reply.update((PREFIX + "/audit", PREFIX + "/retention"))
    for _ in range(3):
        await world.runtime.worker.audit_tick()
        await world.runtime.worker.retention_tick()
    async with world.inventory.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(InventoryAuditOutboxRow)
                .where(InventoryAuditOutboxRow.delivered_at.is_(None))
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(InventoryRetentionOutboxRow)
                .where(InventoryRetentionOutboxRow.delivered_at.is_(None))
            )
            == 0
        )
    async with world.main.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 1
        assert (await verify_chain(session)).ok


def test_service_keeps_the_complete_public_permission_table(remote_world: RemoteWorld) -> None:
    """The independent app mounts every existing route with its original permission."""
    from telescreen.core.permissions import Permission
    from tests.api.v12_rbac.test_v12_rbac_router_permissions import _api_routes
    from tests.e2e.test_route_permissions_v14 import _expected, _permissions

    actual: list[tuple[str, str, Permission]] = []
    for route in _api_routes(remote_world.inventory_app.router):
        if route.path.startswith("/api/data/"):
            permissions: tuple[Permission, ...] = _permissions(route.dependant)
            assert len(permissions) == 1
            assert route.methods is not None
            for method in route.methods:
                actual.append((method, route.path, permissions[0]))
    assert sorted(actual) == sorted(_expected())
