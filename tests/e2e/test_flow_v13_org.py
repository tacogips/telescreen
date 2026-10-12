"""End-to-end v1.3 organization ownership, member lifecycle, and self-token flow."""

from pathlib import Path

import httpx
import pytest

from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import IssueRow
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world
from tests.support.samples import aws_access_key_id


@pytest.mark.asyncio
async def test_e4_ownership_member_deactivation_and_self_token(tmp_path: Path) -> None:
    """Match an ownership rule, release deactivated ownership, and use a self token."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    try:
        team: httpx.Response = await world.client.post(
            "/api/teams",
            headers=bearer(world.admin_token),
            json={"slug": "e4-secops", "name": "E4 Security"},
        )
        assert team.status_code in (200, 201), team.text
        rule: httpx.Response = await world.client.post(
            "/api/ownership-rules",
            headers=bearer(world.admin_token),
            json={"source": "custom:v13-e4", "pattern": "src/**", "team": "e4-secops"},
        )
        assert rule.status_code == 201, rule.text
        matched: httpx.Response = await world.client.post(
            "/api/ownership-rules/test",
            headers=bearer(world.admin_token),
            json={"source": "custom:v13-e4", "locator": "src/config.py"},
        )
        assert matched.status_code == 200, matched.text
        assert matched.json()["rule_id"] == rule.json()["id"]
        assert matched.json()["team"] == "e4-secops"

        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "v13-e4",
                "kind": "custom",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": "{}",
                "credential_ref": None,
                "enabled": True,
            },
        )
        assert collector.status_code in (200, 201), collector.text
        async with world.database.sessions.begin() as session:
            ingest_token: CreatedToken = await TokenService(
                world.container.audit, world.clock
            ).create(session, "v13-e4-ingest", frozenset((ApiScope.INGEST,)), None)
        scan: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            headers=bearer(ingest_token.token),
            json={
                "source": "custom:v13-e4",
                "resources": [
                    {
                        "locator": "src/config.py",
                        "segments": [{"locator": "L1", "text": aws_access_key_id().value}],
                    }
                ],
            },
        )
        assert scan.status_code == 200, scan.text
        issue_id: str = scan.json()["issue_ids"][0]
        async with world.database.sessions() as session:
            routed_issue: IssueRow | None = await session.get(IssueRow, issue_id)
            assert routed_issue is not None
            assert routed_issue.assignee_team_id == team.json()["id"]

        created_member: httpx.Response = await world.client.post(
            "/api/members",
            headers=bearer(world.admin_token),
            json={"handle": "e4-owner", "display_name": "E4 Owner", "email": "owner@example.test"},
        )
        assert created_member.status_code == 201, created_member.text
        role: httpx.Response = await world.client.put(
            "/api/users/e4-owner/role",
            headers=bearer(world.admin_token),
            json={"role": "responder"},
        )
        assert role.status_code == 200, role.text
        assigned: httpx.Response = await world.client.patch(
            f"/api/issues/{issue_id}",
            headers=bearer(world.admin_token),
            json={"assignee_ref": "member:e4-owner"},
        )
        assert assigned.status_code == 200, assigned.text
        async with world.database.sessions() as session:
            stored_issue: IssueRow | None = await session.get(IssueRow, issue_id)
            assert stored_issue is not None and stored_issue.assignee_member_id is not None

        member_token: httpx.Response = await world.client.post(
            "/api/tokens",
            headers=bearer(world.admin_token),
            json={"label": "e4-self-bootstrap", "scopes": ["read"], "member_handle": "e4-owner"},
        )
        assert member_token.status_code == 201, member_token.text
        self_token: httpx.Response = await world.client.post(
            "/api/auth/me/tokens",
            headers=bearer(member_token.json()["token"]),
            json={"label": "e4-self-read", "scopes": ["read"]},
        )
        assert self_token.status_code == 201, self_token.text
        self_read: httpx.Response = await world.client.get(
            "/api/issues", headers=bearer(self_token.json()["token"])
        )
        assert self_read.status_code == 200, self_read.text

        deactivated: httpx.Response = await world.client.post(
            "/api/members/e4-owner/deactivate", headers=bearer(world.admin_token)
        )
        assert deactivated.status_code == 204, deactivated.text
        async with world.database.sessions() as session:
            released_issue: IssueRow | None = await session.get(IssueRow, issue_id)
            assert released_issue is not None
            assert released_issue.assignee_member_id is None
    finally:
        await world.close()
