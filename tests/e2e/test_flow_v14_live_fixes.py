"""Run-2 regression coverage for live data inventory defects."""

from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from telescreen.api.routers.ingest import IngestScanBody, ResourceBody
from telescreen.core.vocabulary import ApiScope
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world


@pytest.mark.asyncio
async def test_push_discovery_displays_collector_actor_and_null_occurred_at(tmp_path: Path) -> None:
    """A discovered push event shows its collector name and recorded timestamp."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder(), independent_sessions=True)
    try:
        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "v14-live-actor",
                "kind": "custom",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": "{}",
                "credential_ref": None,
                "enabled": True,
                "inventory_mode": "all",
            },
        )
        assert collector.status_code == 200, collector.text
        async with world.database.sessions.begin() as session:
            token: CreatedToken = await TokenService(world.container.audit, world.clock).create(
                session, "v14-live-actor-ingest", frozenset((ApiScope.INGEST,)), None
            )
        pushed: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            headers=bearer(token.token),
            json=asdict(
                IngestScanBody(
                    "custom:v14-live-actor",
                    [ResourceBody(locator="example.test/live", complete=True, removed=False)],
                )
            ),
        )
        assert pushed.status_code == 200, pushed.text
        await world.drain()
        assets: httpx.Response = await world.client.get(
            "/api/data/assets?source=custom:v14-live-actor", headers=bearer(world.admin_token)
        )
        assert len(assets.json()["items"]) == 1
        asset_id: str = assets.json()["items"][0]["id"]
        events: httpx.Response = await world.client.get(
            f"/api/data/assets/{asset_id}/events", headers=bearer(world.admin_token)
        )
        assert any(
            event["kind"] == "discovered"
            and event["actor"]["kind"] == "system"
            and event["actor"]["display_name"] == "v14-live-actor"
            and event["actor"]["ref"] == f"collector:{collector.json()['id']}"
            and event["occurred_at"] is None
            and event["recorded_at"] is not None
            for event in events.json()["items"]
        )
    finally:
        await world.close()
