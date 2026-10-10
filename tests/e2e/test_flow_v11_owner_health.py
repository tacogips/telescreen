"""End-to-end v1.1 owner-review, collector-health, and coverage routes."""

import json
from pathlib import Path

import httpx
import pytest

from tests.e2e.harness import E2EWorld, OutboundRecorder, admin_post, bearer, make_world


@pytest.mark.asyncio
async def test_owner_review_workflow_and_health_coverage_routes(tmp_path: Path) -> None:
    """Load the owner workflow and exercise health and scan-coverage projections."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    try:
        source: str = Path("examples/workflows/owner-review.yaml").read_text()
        uploaded: httpx.Response = await admin_post(
            world,
            "/api/workflows",
            json.dumps({"source_format": "yaml", "source_text": source}),
        )
        assert uploaded.status_code == 201, uploaded.text
        collector: httpx.Response = await admin_post(
            world,
            "/api/collectors",
            json.dumps(
                {
                    "name": "v11-health-coverage",
                    "kind": "custom",
                    "trigger_mode": "event",
                    "cron": None,
                    "sweep_cron": None,
                    "selector_json": "{}",
                    "credential_ref": None,
                    "enabled": True,
                }
            ),
        )
        assert collector.status_code == 200, collector.text
        config_id: str = collector.json()["id"]
        health: httpx.Response = await world.client.get(
            "/api/collectors", headers=bearer(world.admin_token)
        )
        coverage: httpx.Response = await world.client.get(
            f"/api/collectors/{config_id}/coverage", headers=bearer(world.admin_token)
        )
        resources: httpx.Response = await world.client.get(
            f"/api/collectors/{config_id}/coverage/resources", headers=bearer(world.admin_token)
        )
        assert health.status_code == coverage.status_code == resources.status_code == 200
    finally:
        await world.close()
