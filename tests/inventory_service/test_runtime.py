"""Standalone inventory routes authenticate from the local directory replica."""

import httpx

from telescreen.inventory_service.transport import PREFIX
from tests.inventory_service.conftest import InventoryWorld


async def test_inventory_routes_use_local_token_replica(inventory_world: InventoryWorld) -> None:
    """Public inventory reads work with replica auth and retain service auth."""
    world: InventoryWorld = inventory_world
    response: httpx.Response = await world.client.get("/api/data/assets", headers=world.headers)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    health: httpx.Response = await world.client.get(PREFIX + "/health", headers=world.headers)
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "directory_revision": 1}
    no_service_token: httpx.Response = await world.client.get(
        "/api/data/assets", headers=(("authorization", f"Bearer {world.token}"),)
    )
    assert no_service_token.status_code == 401


async def test_inventory_has_no_retention_callback_or_bureau_destination(
    inventory_world: InventoryWorld,
) -> None:
    """Inventory exposes no eligibility callback and has no Telescreen origin configured."""
    world: InventoryWorld = inventory_world
    response: httpx.Response = await world.client.post(
        PREFIX + "/retention-status", headers=world.headers, content="{}"
    )
    assert response.status_code == 404
    assert world.runtime.context.authentication is not None
    assert all("bureau" not in url for url in (world.network.requests or ()))
