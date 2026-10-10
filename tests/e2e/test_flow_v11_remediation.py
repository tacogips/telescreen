"""End-to-end v1.1 linked remediation and verification request flow."""

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import IssueRow
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.e2e.harness import E2EWorld, OutboundRecorder, admin_post, bearer, make_world
from tests.support.samples import github_token


@pytest.mark.asyncio
async def test_linked_remediation_enters_pending_verification(tmp_path: Path) -> None:
    """Request linked verification and confirm both related issues await rescanning."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    try:
        async with world.database.sessions.begin() as session:
            ingest: CreatedToken = await TokenService(world.container.audit, world.clock).create(
                session, "v11-remediation-ingest", frozenset((ApiScope.INGEST,)), None
            )
        create: httpx.Response = await admin_post(
            world,
            "/api/collectors",
            json.dumps(
                {
                    "name": "v11-remediation",
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
        assert create.status_code == 200
        scan: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            headers=bearer(ingest.token),
            json={
                "source": "custom:v11-remediation",
                "resources": [
                    {
                        "locator": locator,
                        "segments": [{"locator": "line:1", "text": github_token().value}],
                    }
                    for locator in ("first.txt", "second.txt")
                ],
            },
        )
        assert scan.status_code == 200, scan.text
        issue_ids: list[str] = scan.json()["issue_ids"]
        assert len(issue_ids) == 2
        response: httpx.Response = await admin_post(
            world,
            f"/api/issues/{issue_ids[0]}/remediation",
            json.dumps({"action": "remove", "include_linked": True}),
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["issue_ids"]) == 2
        async with world.database.sessions() as session:
            rows: tuple[IssueRow, ...] = tuple(
                await session.scalars(select(IssueRow).where(IssueRow.id.in_(issue_ids)))
            )
        assert len(rows) == 2
        assert all(row.status == "pending_verification" for row in rows)
    finally:
        await world.close()
