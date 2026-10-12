"""End-to-end v1.3 issue stream, saved view, bulk, and merge flow."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import MemberRow
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world
from tests.support.samples import aws_access_key_id, github_token


@pytest.mark.asyncio
async def test_e1_stream_query_views_bulk_and_merge(tmp_path: Path) -> None:
    """Filter seeded issues, share a view, bulk resolve, and restore a merge child."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    try:
        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "v13-e1",
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
            ).create(session, "v13-e1-ingest", frozenset((ApiScope.INGEST,)), None)
        scan_headers: tuple[tuple[str, str], ...] = bearer(ingest_token.token)

        async def scan(first_text: str, second_text: str) -> tuple[str, str]:
            response: httpx.Response = await world.client.post(
                "/api/ingest/v1/scan",
                headers=scan_headers,
                json={
                    "source": "custom:v13-e1",
                    "resources": [
                        {
                            "locator": "first.py",
                            "segments": [{"locator": "L1", "text": first_text}],
                        },
                        {
                            "locator": "second.py",
                            "segments": [{"locator": "L1", "text": second_text}],
                        },
                    ],
                },
            )
            assert response.status_code == 200, response.text
            issue_ids: list[str] = response.json()["issue_ids"]
            assert len(issue_ids) == 2
            return issue_ids[0], issue_ids[1]

        first_sample: str = aws_access_key_id().value
        second_sample: str = github_token().value
        first_id: str
        second_id: str
        first_id, second_id = await scan(first_sample, second_sample)
        for _ in range(5):
            repeated: httpx.Response = await world.client.post(
                "/api/ingest/v1/scan",
                headers=scan_headers,
                json={
                    "source": "custom:v13-e1",
                    "resources": [
                        {
                            "locator": "first.py",
                            "segments": [{"locator": "L1", "text": first_sample}],
                        }
                    ],
                },
            )
            assert repeated.status_code == 200, repeated.text
        team: httpx.Response = await world.client.post(
            "/api/teams",
            headers=bearer(world.admin_token),
            json={"slug": "e1-assigned", "name": "E1 Assigned"},
        )
        assert team.status_code in (200, 201), team.text
        assigned: httpx.Response = await world.client.patch(
            f"/api/issues/{second_id}",
            headers=bearer(world.admin_token),
            json={"assignee_ref": "team:e1-assigned"},
        )
        assert assigned.status_code == 200, assigned.text

        async def query_ids(query: str) -> set[str]:
            response: httpx.Response = await world.client.get(
                "/api/issues", params={"query": query}, headers=bearer(world.admin_token)
            )
            assert response.status_code == 200, response.text
            return {item["id"] for item in response.json()["items"]}

        assert await query_ids("!status:resolved") == {first_id, second_id}
        assert await query_ids("has:assignee") == {second_id}
        assert await query_ids("first_seen:-7d") == {first_id, second_id}
        assert await query_ids("times_seen:>5") == {first_id}

        counts: httpx.Response = await world.client.get(
            "/api/issues/counts", headers=bearer(world.admin_token)
        )
        assert counts.status_code == 200, counts.text
        unresolved_count: int = next(
            tab["count"] for tab in counts.json()["tabs"] if tab["tab"] == "unresolved"
        )
        assert unresolved_count == 2

        member: httpx.Response = await world.client.post(
            "/api/members",
            headers=bearer(world.admin_token),
            json={"handle": "e1-admin", "display_name": "E1 Admin", "role": "admin"},
        )
        assert member.status_code == 201, member.text
        member_role: httpx.Response = await world.client.put(
            "/api/users/e1-admin/role",
            headers=bearer(world.admin_token),
            json={"role": "admin"},
        )
        assert member_role.status_code == 200, member_role.text
        async with world.database.sessions.begin() as session:
            member_row: MemberRow | None = await session.scalar(
                select(MemberRow).where(MemberRow.handle == "e1-admin")
            )
            assert member_row is not None
            member_token: CreatedToken = await TokenService(
                world.container.audit, world.clock
            ).create(
                session,
                "v13-e1-member",
                frozenset((ApiScope.READ, ApiScope.WRITE)),
                member_row.id,
            )
        view: httpx.Response = await world.client.post(
            "/api/views",
            headers=bearer(member_token.token),
            json={
                "name": "E1 open issues",
                "query": "is:unresolved",
                "sort": "priority",
                "period": "14d",
                "visibility": "shared",
            },
        )
        assert view.status_code == 201, view.text
        view_id: str = view.json()["id"]
        defaulted: httpx.Response = await world.client.put(
            "/api/views/default",
            headers=bearer(member_token.token),
            json={"view_id": view_id},
        )
        assert defaulted.status_code == 200, defaulted.text
        owner_views: httpx.Response = await world.client.get(
            "/api/views", headers=bearer(member_token.token)
        )
        assert owner_views.status_code == 200, owner_views.text
        assert any(
            item["id"] == view_id and item["is_default"] is True
            for item in owner_views.json()["items"]
        )
        second_member: httpx.Response = await world.client.post(
            "/api/members",
            headers=bearer(world.admin_token),
            json={"handle": "e1-reader", "display_name": "E1 Reader", "role": "responder"},
        )
        assert second_member.status_code == 201, second_member.text
        reader_role: httpx.Response = await world.client.put(
            "/api/users/e1-reader/role",
            headers=bearer(world.admin_token),
            json={"role": "responder"},
        )
        assert reader_role.status_code == 200, reader_role.text
        async with world.database.sessions.begin() as session:
            reader_row: MemberRow | None = await session.scalar(
                select(MemberRow).where(MemberRow.handle == "e1-reader")
            )
            assert reader_row is not None
            reader_token: CreatedToken = await TokenService(
                world.container.audit, world.clock
            ).create(
                session,
                "v13-e1-reader",
                frozenset((ApiScope.READ, ApiScope.WRITE)),
                reader_row.id,
            )
        listed: httpx.Response = await world.client.get(
            "/api/views", headers=bearer(reader_token.token)
        )
        assert listed.status_code == 200, listed.text
        assert any(item["id"] == view_id for item in listed.json()["items"])

        merged: httpx.Response = await world.client.post(
            "/api/issues/merge",
            headers=bearer(world.admin_token),
            json={"primary_id": first_id, "ids": [second_id]},
        )
        assert merged.status_code == 200, merged.text
        detail: httpx.Response = await world.client.get(
            f"/api/issues/{second_id}", headers=bearer(world.admin_token)
        )
        assert detail.status_code == 200 and detail.json()["merged_into"]["id"] == first_id
        unmerged: httpx.Response = await world.client.post(
            f"/api/issues/{first_id}/unmerge",
            headers=bearer(world.admin_token),
            json={"ids": [second_id]},
        )
        assert unmerged.status_code == 200, unmerged.text
        restored: httpx.Response = await world.client.get(
            f"/api/issues/{second_id}", headers=bearer(world.admin_token)
        )
        assert restored.status_code == 200 and restored.json()["merged_into"] is None
        bulk: httpx.Response = await world.client.post(
            "/api/issues/bulk",
            headers=bearer(world.admin_token),
            json={
                "action": "resolve",
                "query": "is:unresolved",
                "params": {"reason": "false_positive", "note": "Confirmed test fixture."},
            },
        )
        assert bulk.status_code == 200, bulk.text
        assert bulk.json()["matched"] == bulk.json()["processed"] == 2
        assert set(bulk.json()["succeeded"]) == {first_id, second_id}
    finally:
        await world.close()
