"""End-to-end push ingestion through the assembled Reporting Bureau."""

import json
from pathlib import Path

import httpx
import pytest

from telescreen.core.vocabulary import ApiScope
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.e2e.harness import E2EWorld, OutboundRecorder, admin_post, bearer, make_world
from tests.support.samples import (
    Sample,
    assert_no_leaks,
    aws_access_key_id,
    aws_secret_access_key,
)


@pytest.mark.asyncio
async def test_push_scan_masks_findings_and_rejects_raw_external_findings(tmp_path: Path) -> None:
    """Scan pushed content transiently and enforce the masked finding contract."""
    sample_id: Sample = aws_access_key_id()
    sample_secret: Sample = aws_secret_access_key()
    outbound: OutboundRecorder = OutboundRecorder()
    world: E2EWorld = await make_world(tmp_path, (), outbound)
    try:
        async with world.database.sessions.begin() as session:
            ingest_token: CreatedToken = await TokenService(
                world.container.audit, world.clock
            ).create(session, "e2e-ingest", frozenset((ApiScope.INGEST,)), None)
        headers: tuple[tuple[str, str], ...] = bearer(ingest_token.token) + (
            ("Content-Type", "application/json"),
        )
        create: httpx.Response = await admin_post(
            world,
            "/api/collectors",
            json.dumps(
                {
                    "name": "e2e-crawler",
                    "kind": "custom",
                    "trigger_mode": "event",
                    "cron": None,
                    "sweep_cron": None,
                    "selector_json": json.dumps({"paths": ["src/**"]}),
                    "credential_ref": None,
                    "enabled": True,
                }
            ),
        )
        assert create.status_code == 200, create.text
        source: str = "custom:e2e-crawler"
        scan: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            content=json.dumps(
                {
                    "source": source,
                    "resources": [
                        {
                            "locator": "repo/src/settings.py",
                            "complete": True,
                            "segments": [
                                {
                                    "locator": "L1",
                                    "text": f"key={sample_id.value} secret={sample_secret.value}",
                                }
                            ],
                        }
                    ],
                }
            ),
            headers=headers,
        )
        world.responses.append(scan.text)
        assert scan.status_code == 200, scan.text
        assert scan.json()["findings"] >= 1
        assert scan.json()["issue_ids"]
        issue_id: str = scan.json()["issue_ids"][0]
        issue: httpx.Response = await world.client.get(
            f"/api/issues/{issue_id}/events", headers=bearer(world.admin_token)
        )
        world.responses.append(issue.text)
        assert issue.status_code == 200, issue.text
        assert sample_id.value not in issue.text and sample_secret.value not in issue.text
        assert "masked_snippet" in issue.text

        raw_finding: httpx.Response = await world.client.post(
            "/api/ingest/v1/findings",
            content=json.dumps(
                {
                    "source": source,
                    "locator": "repo/src/settings.py",
                    "complete": True,
                    "findings": [
                        {
                            "locator": "repo/src/settings.py",
                            "rule_id": "aws_access_key_id",
                            "rule_class": "credential",
                            "masked_snippet": sample_id.value,
                            "location": "L1",
                            "confidence": 1.0,
                            "external_fingerprint": "external-fingerprint",
                        }
                    ],
                }
            ),
            headers=headers,
        )
        world.responses.append(raw_finding.text)
        assert raw_finding.status_code == 422
        assert "raw_value_detected" in raw_finding.text
        assert_no_leaks(raw_finding.text, "raw finding rejection")

        unknown: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            content=json.dumps({"source": "custom:nope", "resources": []}),
            headers=headers,
        )
        world.responses.append(unknown.text)
        assert unknown.status_code == 404 and "unknown_source" in unknown.text
        config: httpx.Response = await world.client.get(
            "/api/ingest/v1/sources/e2e-crawler/config", headers=headers
        )
        world.responses.append(config.text)
        assert config.status_code == 200
        assert config.json()["paths"] == ["src/**"]
        for response_text in world.responses:
            assert_no_leaks(response_text, "ingestion API response")
    finally:
        await world.close()
