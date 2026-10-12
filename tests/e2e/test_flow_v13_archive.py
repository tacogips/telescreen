"""End-to-end v1.3 archive, escalation, and archived merge safety flow."""

from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.clock import ensure_utc
from telescreen.core.json import JsonValue, parse_json
from telescreen.core.vocabulary import ApiScope, IssueStatus
from telescreen.db.models import AlertFireRow, IssueRow
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world
from tests.support.samples import aws_access_key_id, github_token


@pytest.mark.asyncio
async def test_e2_archive_escalation_alert_and_archived_merge_safety(tmp_path: Path) -> None:
    """Keep an archived primary bounded through merge and escalate on its fifth finding."""

    def slack_reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"ok": True, "channel": "C0123ABCDE"})

    outbound: OutboundRecorder = OutboundRecorder()
    outbound.handlers.append(("slack.com", slack_reply))
    world: E2EWorld = await make_world(
        tmp_path,
        (
            ("TELESCREEN_SLACK_BOT_TOKEN", "env:TELESCREEN_E2E_SLACK_TOKEN"),
            ("TELESCREEN_E2E_SLACK_TOKEN", "e2e-slack-test-token"),
        ),
        outbound,
    )
    try:
        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "v13-e2",
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
            ).create(session, "v13-e2-ingest", frozenset((ApiScope.INGEST,)), None)

        async def ingest(locator: str, sample: str) -> str:
            response: httpx.Response = await world.client.post(
                "/api/ingest/v1/scan",
                headers=bearer(ingest_token.token),
                json={
                    "source": "custom:v13-e2",
                    "resources": [
                        {"locator": locator, "segments": [{"locator": "L1", "text": sample}]}
                    ],
                },
            )
            assert response.status_code == 200, response.text
            issue_ids: list[str] = response.json()["issue_ids"]
            assert len(issue_ids) == 1
            return issue_ids[0]

        primary_sample: str = aws_access_key_id().value
        child_sample: str = github_token().value
        primary_id: str = await ingest("primary.py", primary_sample)
        child_id: str = await ingest("child.py", child_sample)
        for _ in range(39):
            assert await ingest("child.py", child_sample) == child_id

        rule: httpx.Response = await world.client.post(
            "/api/alert-rules",
            headers=bearer(world.admin_token),
            json={
                "name": "e2-escalation",
                "trigger": {"kind": "issue_escalating"},
                "filters": {},
                "actions": {
                    "targets": [{"recipients": ["slack_channel:C0123ABCDE"], "channels": ["slack"]}]
                },
                "action_interval_minutes": 60,
            },
        )
        assert rule.status_code == 201, rule.text

        archived: httpx.Response = await world.client.post(
            f"/api/issues/{primary_id}/archive",
            headers=bearer(world.admin_token),
            json={"kind": "until_findings", "count": 5},
        )
        assert archived.status_code == 200, archived.text
        async with world.database.sessions() as session:
            archived_primary: IssueRow | None = await session.get(IssueRow, primary_id)
            assert archived_primary is not None and archived_primary.archive_until is not None
            initial_until: datetime = ensure_utc(archived_primary.archive_until)
        merged: httpx.Response = await world.client.post(
            "/api/issues/merge",
            headers=bearer(world.admin_token),
            json={"primary_id": primary_id, "ids": [child_id]},
        )
        assert merged.status_code == 200, merged.text

        for _ in range(4):
            assert await ingest("primary.py", primary_sample) == primary_id
        async with world.database.sessions() as session:
            stored_primary: IssueRow | None = await session.get(IssueRow, primary_id)
            assert stored_primary is not None
            assert stored_primary.status == IssueStatus.ARCHIVED.value
            assert stored_primary.archive_until is not None
            assert ensure_utc(stored_primary.archive_until) == ensure_utc(initial_until)
        assert await ingest("primary.py", primary_sample) == primary_id
        async with world.database.sessions() as session:
            escalated_primary: IssueRow | None = await session.get(IssueRow, primary_id)
            assert escalated_primary is not None
            assert escalated_primary.status == IssueStatus.ESCALATING.value
        history: httpx.Response = await world.client.get(
            f"/api/alert-rules/{rule.json()['id']}/history",
            headers=bearer(world.admin_token),
        )
        assert history.status_code == 200, history.text
        history_document: JsonValue = parse_json(history.content)
        fires_value: JsonValue | None = history_document.get("items")
        assert fires_value is not None and fires_value.as_list() is not None
        fires: tuple[JsonValue, ...] = fires_value.as_list() or ()
        assert len(fires) == 1
        fire_issue: JsonValue | None = fires[0].get("issue_id")
        fire_kind: JsonValue | None = fires[0].get("trigger_kind")
        assert fire_issue is not None and fire_issue.as_str() == primary_id
        assert fire_kind is not None and fire_kind.as_str() == "issue_escalating"

        unmerged: httpx.Response = await world.client.post(
            f"/api/issues/{primary_id}/unmerge",
            headers=bearer(world.admin_token),
            json={"ids": [child_id]},
        )
        assert unmerged.status_code == 200, unmerged.text
        for _ in range(4):
            assert await ingest("child.py", child_sample) == child_id
        async with world.database.sessions() as session:
            stored_child: IssueRow | None = await session.get(IssueRow, child_id)
            assert stored_child is not None
            assert stored_child.status == IssueStatus.ARCHIVED.value
        assert await ingest("child.py", child_sample) == child_id
        async with world.database.sessions() as session:
            escalated_child: IssueRow | None = await session.get(IssueRow, child_id)
            assert escalated_child is not None
            assert escalated_child.status == IssueStatus.ESCALATING.value

        reopened_primary: httpx.Response = await world.client.patch(
            f"/api/issues/{primary_id}",
            headers=bearer(world.admin_token),
            json={"status": "unresolved"},
        )
        assert reopened_primary.status_code == 200, reopened_primary.text
        rearchived_primary: httpx.Response = await world.client.post(
            f"/api/issues/{primary_id}/archive",
            headers=bearer(world.admin_token),
            json={"kind": "until_findings", "count": 1},
        )
        assert rearchived_primary.status_code == 200, rearchived_primary.text
        assert await ingest("primary.py", primary_sample) == primary_id
        async with world.database.sessions() as session:
            interval_fires: tuple[AlertFireRow, ...] = tuple(
                await session.scalars(
                    select(AlertFireRow).where(AlertFireRow.rule_id == rule.json()["id"])
                )
            )
        primary_suppressed: tuple[AlertFireRow, ...] = tuple(
            fire
            for fire in interval_fires
            if fire.issue_id == primary_id and fire.outcome == "suppressed_interval"
        )
        child_delivered: tuple[AlertFireRow, ...] = tuple(
            fire
            for fire in interval_fires
            if fire.issue_id == child_id and fire.outcome == "delivered"
        )
        assert len(interval_fires) == 3
        assert len(primary_suppressed) == 1
        assert len(child_delivered) == 1
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_e2_duration_archive_expiry_reopens_for_review(tmp_path: Path) -> None:
    """Expire a duration archive through the scheduler without wall-clock sleep."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    try:
        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "v13-e2-expiry",
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
            ).create(session, "v13-e2-expiry-ingest", frozenset((ApiScope.INGEST,)), None)
        scan: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            headers=bearer(ingest_token.token),
            json={
                "source": "custom:v13-e2-expiry",
                "resources": [
                    {
                        "locator": "expiry.py",
                        "segments": [{"locator": "L1", "text": aws_access_key_id().value}],
                    }
                ],
            },
        )
        assert scan.status_code == 200, scan.text
        issue_id: str = scan.json()["issue_ids"][0]
        archived: httpx.Response = await world.client.post(
            f"/api/issues/{issue_id}/archive",
            headers=bearer(world.admin_token),
            json={"kind": "for_duration", "duration_minutes": 60},
        )
        assert archived.status_code == 200, archived.text
        world.clock.advance(timedelta(minutes=61))
        await world.container.worker.scheduler.tick()
        async with world.database.sessions() as session:
            expired: IssueRow | None = await session.get(IssueRow, issue_id)
            assert expired is not None
            assert expired.status == IssueStatus.UNRESOLVED.value
            assert expired.needs_review is True
            assert expired.archive_until is None
    finally:
        await world.close()
