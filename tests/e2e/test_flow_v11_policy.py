"""End-to-end v1.1 placement policy and stale-data flow."""

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import IssueHistoryRow, IssueRow
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.e2e.harness import E2EWorld, OutboundRecorder, admin_post, bearer, make_world
from tests.support.samples import github_token


async def _scan_issue(world: E2EWorld) -> str:
    """Create one custom source issue through the assembled API."""
    collector: httpx.Response = await admin_post(
        world,
        "/api/collectors",
        json.dumps(
            {
                "name": "policy-e2e",
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
    async with world.database.sessions.begin() as session:
        ingest: CreatedToken = await TokenService(world.container.audit, world.clock).create(
            session, "policy-ingest", frozenset((ApiScope.INGEST,)), None
        )
    scan: httpx.Response = await world.client.post(
        "/api/ingest/v1/scan",
        headers=bearer(ingest.token),
        json={
            "source": "custom:policy-e2e",
            "resources": [
                {
                    "locator": "policy.txt",
                    "segments": [{"locator": "line:1", "text": github_token().value}],
                }
            ],
        },
    )
    assert scan.status_code == 200, scan.text
    return scan.json()["issue_ids"][0]


@pytest.mark.asyncio
async def test_policy_activation_and_max_age_condition(tmp_path: Path) -> None:
    """Prove max-age reevaluation and that a matching violate policy wins."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    try:
        issue_id: str = await _scan_issue(world)
        created: httpx.Response = await admin_post(
            world,
            "/api/policies",
            json.dumps(
                {
                    "source_format": "yaml",
                    "source_text": (
                        "name: stale-data\neffect: allow\n"
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
        await world.drain()
        async with world.database.sessions() as session:
            fresh: IssueRow | None = await session.get(IssueRow, issue_id)
        assert fresh is not None and (fresh.status, fresh.resolution) == (
            "allowed",
            "policy_changed",
        )
        world.clock.advance(timedelta(days=2))
        await world.container.worker.scheduler.tick()
        await world.drain()
        async with world.database.sessions() as session:
            stale: IssueRow | None = await session.get(IssueRow, issue_id)
            history: tuple[IssueHistoryRow, ...] = tuple(
                await session.scalars(
                    select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue_id)
                )
            )
        assert stale is not None and (stale.status, stale.resolution) == ("unresolved", None)
        assert any(row.kind == "policy_reevaluated" for row in history)
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_matching_violate_overrides_matching_allow(tmp_path: Path) -> None:
    """A matching violate policy reopens an allowed issue and records the transition."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    try:
        issue_id: str = await _scan_issue(world)
        allow: httpx.Response = await admin_post(
            world,
            "/api/policies",
            json.dumps(
                {"source_format": "yaml", "source_text": "name: fresh-allow\neffect: allow\n"}
            ),
        )
        assert allow.status_code == 201, allow.text
        activate_allow: httpx.Response = await admin_post(
            world, "/api/policies/fresh-allow/activate", '{"version":1}'
        )
        assert activate_allow.status_code == 200, activate_allow.text
        await world.drain()
        async with world.database.sessions() as session:
            allowed: IssueRow | None = await session.get(IssueRow, issue_id)
        assert allowed is not None and (allowed.status, allowed.resolution) == (
            "allowed",
            "policy_changed",
        )

        violate: httpx.Response = await admin_post(
            world,
            "/api/policies",
            json.dumps(
                {
                    "source_format": "yaml",
                    "source_text": "name: matching-violate\neffect: violate\n",
                }
            ),
        )
        assert violate.status_code == 201, violate.text
        activate_violate: httpx.Response = await admin_post(
            world, "/api/policies/matching-violate/activate", '{"version":1}'
        )
        assert activate_violate.status_code == 200, activate_violate.text
        await world.drain()
        async with world.database.sessions() as session:
            reopened: IssueRow | None = await session.get(IssueRow, issue_id)
            history: tuple[IssueHistoryRow, ...] = tuple(
                await session.scalars(
                    select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue_id)
                )
            )
        assert reopened is not None and (reopened.status, reopened.resolution) == (
            "unresolved",
            None,
        )
        assert any(
            row.kind == "policy_reevaluated"
            and row.from_value == "allowed"
            and row.to_value == "unresolved"
            for row in history
        )
    finally:
        await world.close()
