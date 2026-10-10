"""End-to-end v1.1 placement policy and stale-data flow."""

import json
from pathlib import Path

import httpx
import pytest

from tests.e2e.harness import E2EWorld, OutboundRecorder, admin_post, bearer, make_world


@pytest.mark.asyncio
async def test_policy_activation_and_max_age_condition(tmp_path: Path) -> None:
    """Create and activate a policy carrying the stale-data condition over the API."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    try:
        created: httpx.Response = await admin_post(
            world,
            "/api/policies",
            json.dumps(
                {
                    "source_format": "yaml",
                    "source_text": (
                        "name: stale-data\neffect: allow\nmatch:\n  sources: [github]\n"
                        "condition:\n  max_age_days: 1\nroute_to_owner: true\n"
                    ),
                }
            ),
        )
        assert created.status_code == 201, created.text
        activated: httpx.Response = await admin_post(
            world, "/api/policies/stale-data/activate", '{"version":1}'
        )
        assert activated.status_code == 200, activated.text
        listed: httpx.Response = await world.client.get(
            "/api/policies", headers=bearer(world.admin_token)
        )
        assert listed.status_code == 200
        assert any(item["name"] == "stale-data" for item in listed.json()["items"])
    finally:
        await world.close()
