"""End-to-end v1.1 owner-review, collector-health, and coverage routes."""

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from telescreen.api.app import create_app
from telescreen.core.config import load_settings
from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import (
    FindingRow,
    HumanTaskRow,
    IssueHistoryRow,
    IssueRow,
    WorkflowRunRow,
)
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.wiring import Container, build_container, close_container
from tests.e2e.harness import (
    E2EWorld,
    OutboundRecorder,
    PairEnv,
    admin_post,
    bearer,
    make_world,
)
from tests.support.samples import aws_access_key_id, github_token


async def _owner_task(world: E2EWorld, issue_id: str) -> HumanTaskRow:
    """Find the owner-review task created for one issue."""
    async with world.database.sessions() as session:
        task: HumanTaskRow | None = await session.scalar(
            select(HumanTaskRow)
            .join(WorkflowRunRow, WorkflowRunRow.id == HumanTaskRow.run_id)
            .where(WorkflowRunRow.issue_id == issue_id, HumanTaskRow.kind == "owner_review")
        )
    assert task is not None
    return task


async def _owner_issue(world: E2EWorld, ingest_token: str, locator: str) -> str:
    """Ingest a unique rule finding and return its issue id."""
    response: httpx.Response = await world.client.post(
        "/api/ingest/v1/scan",
        headers=bearer(ingest_token),
        json={
            "source": "custom:v11-owner-e2e",
            "resources": [
                {
                    "locator": locator,
                    "segments": [{"locator": "line:1", "text": aws_access_key_id().value}],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["issue_ids"][0]


async def _configure_owner_workflow(world: E2EWorld) -> tuple[str, str]:
    """Create the owner teams, member token, escalation policy and active workflow."""
    for slug in ("security", "secops-tier2"):
        created: httpx.Response = await admin_post(
            world,
            "/api/teams",
            json.dumps({"slug": slug, "name": slug, "email": None, "slack_channel": None}),
        )
        assert created.status_code == 201, created.text
    member: httpx.Response = await admin_post(
        world,
        "/api/members",
        json.dumps({"handle": "alice", "display_name": "Alice"}),
    )
    assert member.status_code == 201, member.text
    member_id: str = member.json()["id"]
    membership: httpx.Response = await world.client.put(
        "/api/teams/security/members",
        json={"handles": ["alice"]},
        headers=bearer(world.admin_token),
    )
    assert membership.status_code == 204, membership.text
    async with world.database.sessions.begin() as session:
        token: CreatedToken = await TokenService(world.container.audit, world.clock).create(
            session,
            "v11-owner-member",
            frozenset((ApiScope.READ, ApiScope.WRITE)),
            member_id,
        )
        ingest: CreatedToken = await TokenService(world.container.audit, world.clock).create(
            session, "v11-owner-ingest", frozenset((ApiScope.INGEST,)), None
        )
    policy: httpx.Response = await admin_post(
        world,
        "/api/escalation-policies",
        json.dumps(
            {
                "source_format": "json",
                "tiers_json": json.dumps(
                    {
                        "name": "secops-standard",
                        "tiers": [
                            {
                                "after": "P2D",
                                "assign_to": "team:secops-tier2",
                                "notify": [],
                                "channels": [],
                            }
                        ],
                    }
                ),
            }
        ),
    )
    assert policy.status_code == 201, policy.text
    workflow: httpx.Response = await admin_post(
        world,
        "/api/workflows",
        json.dumps(
            {
                "source_format": "yaml",
                "source_text": Path("examples/workflows/owner-review.yaml").read_text(),
            }
        ),
    )
    assert workflow.status_code == 201, workflow.text
    activated: httpx.Response = await admin_post(
        world, "/api/workflows/owner-review/activate", '{"version":1}'
    )
    assert activated.status_code == 200, activated.text
    collector: httpx.Response = await admin_post(
        world,
        "/api/collectors",
        json.dumps(
            {
                "name": "v11-owner-e2e",
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
    return token.token, ingest.token


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


@pytest.mark.asyncio
async def test_owner_review_remove_and_not_mine_actions(tmp_path: Path) -> None:
    """Member actions request verification and release assignments to the default team."""
    world: E2EWorld = await make_world(
        tmp_path, (("TELESCREEN_DEFAULT_TEAM", "security"),), OutboundRecorder()
    )
    try:
        member_token: str
        ingest_token: str
        member_token, ingest_token = await _configure_owner_workflow(world)
        remove_issue_id: str = await _owner_issue(world, ingest_token, "remove.txt")
        await world.drain()
        remove_task: HumanTaskRow = await _owner_task(world, remove_issue_id)
        assert remove_task.assignee_ref == "team:security"
        remove: httpx.Response = await world.client.post(
            f"/api/human-tasks/{remove_task.id}/complete",
            json={"outcome": "remove"},
            headers=bearer(member_token),
        )
        assert remove.status_code == 200, remove.text
        async with world.database.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, remove_issue_id)
            task: HumanTaskRow | None = await session.get(HumanTaskRow, remove_task.id)
            history: tuple[IssueHistoryRow, ...] = tuple(
                await session.scalars(
                    select(IssueHistoryRow).where(IssueHistoryRow.issue_id == remove_issue_id)
                )
            )
        assert issue is not None and issue.status == "pending_verification"
        assert task is not None and task.status == "completed"
        assert any(row.kind == "verification_requested" for row in history)

        not_mine_issue_id: str = await _owner_issue(world, ingest_token, "not-mine.txt")
        await world.drain()
        not_mine_task: HumanTaskRow = await _owner_task(world, not_mine_issue_id)
        patched: httpx.Response = await world.client.patch(
            f"/api/issues/{not_mine_issue_id}",
            json={"assignee_ref": "member:alice"},
            headers=bearer(world.admin_token),
        )
        assert patched.status_code == 200, patched.text
        world.clock.advance(timedelta(seconds=1))
        completed: httpx.Response = await world.client.post(
            f"/api/human-tasks/{not_mine_task.id}/complete",
            json={"outcome": "not_mine"},
            headers=bearer(member_token),
        )
        assert completed.status_code == 200, completed.text
        async with world.database.sessions() as session:
            issue = await session.get(IssueRow, not_mine_issue_id)
            team_id: str | None = await session.scalar(
                select(IssueRow.assignee_team_id).where(IssueRow.id == not_mine_issue_id)
            )
            history = tuple(
                await session.scalars(
                    select(IssueHistoryRow)
                    .where(
                        IssueHistoryRow.issue_id == not_mine_issue_id,
                        IssueHistoryRow.kind == "assigned",
                    )
                    .order_by(IssueHistoryRow.at.desc())
                )
            )
        assert issue is not None and issue.assignee_member_id is None
        assert team_id is not None
        assert history and history[0].to_value == "team:security"
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_owner_review_min_risk_threshold_controls_escalation(tmp_path: Path) -> None:
    """SLA checks below the risk threshold hold tier zero; equality escalates."""
    world: E2EWorld = await make_world(
        tmp_path, (("TELESCREEN_DEFAULT_TEAM", "security"),), OutboundRecorder()
    )
    try:
        _member_token: str
        ingest_token: str
        _member_token, ingest_token = await _configure_owner_workflow(world)
        low_issue_id: str = await _owner_issue(world, ingest_token, "risk-low.txt")
        await world.drain()
        low_task: HumanTaskRow = await _owner_task(world, low_issue_id)
        async with world.database.sessions() as session:
            low_issue: IssueRow | None = await session.get(IssueRow, low_issue_id)
        assert low_issue is not None and 0 < low_issue.risk_score < 100
        update_high: httpx.Response = await world.client.put(
            "/api/escalation-policies/secops-standard",
            json={
                "source_format": "json",
                "tiers_json": json.dumps(
                    {
                        "name": "secops-standard",
                        "tiers": [
                            {
                                "after": "P2D",
                                "assign_to": "team:secops-tier2",
                                "notify": [],
                                "channels": [],
                                "min_risk": 100,
                            }
                        ],
                    }
                ),
            },
            headers=bearer(world.admin_token),
        )
        assert update_high.status_code == 200, update_high.text
        world.clock.advance(timedelta(days=2, minutes=1))
        await world.drain()
        async with world.database.sessions() as session:
            stopped: HumanTaskRow | None = await session.get(HumanTaskRow, low_task.id)
            history: tuple[IssueHistoryRow, ...] = tuple(
                await session.scalars(
                    select(IssueHistoryRow).where(IssueHistoryRow.issue_id == low_issue_id)
                )
            )
        assert stopped is not None and stopped.tier_index == 0
        assert stopped.assignee_ref == "team:security"
        assert any(
            row.kind == "sla_breached" and row.note == "below risk threshold" for row in history
        )

        equal_issue_id: str = await _owner_issue(world, ingest_token, "risk-equal.txt")
        await world.drain()
        equal_task: HumanTaskRow = await _owner_task(world, equal_issue_id)
        async with world.database.sessions() as session:
            equal_issue: IssueRow | None = await session.get(IssueRow, equal_issue_id)
        assert equal_issue is not None and equal_issue.risk_score >= low_issue.risk_score
        update_equal: httpx.Response = await world.client.put(
            "/api/escalation-policies/secops-standard",
            json={
                "source_format": "json",
                "tiers_json": json.dumps(
                    {
                        "name": "secops-standard",
                        "tiers": [
                            {
                                "after": "P2D",
                                "assign_to": "team:secops-tier2",
                                "notify": [],
                                "channels": [],
                                "min_risk": equal_issue.risk_score,
                            }
                        ],
                    }
                ),
            },
            headers=bearer(world.admin_token),
        )
        assert update_equal.status_code == 200, update_equal.text
        world.clock.advance(timedelta(days=2, minutes=1))
        await world.drain()
        async with world.database.sessions() as session:
            escalated: HumanTaskRow | None = await session.get(HumanTaskRow, equal_task.id)
        assert escalated is not None and escalated.tier_index == 1
        assert escalated.assignee_ref == "team:secops-tier2"
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_fingerprint_key_rotation_preserves_issue_identity(tmp_path: Path) -> None:
    """Rotate value fingerprints while retaining version-one issue identity."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder())
    second_container: Container | None = None
    second_http: httpx.AsyncClient | None = None
    second_client: AsyncClient | None = None
    try:
        collector: httpx.Response = await admin_post(
            world,
            "/api/collectors",
            json.dumps(
                {
                    "name": "v11-key-rotation",
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
                session, "v11-key-rotation-ingest", frozenset((ApiScope.INGEST,)), None
            )
        sample: str = github_token().value
        first_response: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            headers=bearer(ingest.token),
            json={
                "source": "custom:v11-key-rotation",
                "resources": [
                    {
                        "locator": "first.txt",
                        "segments": [{"locator": "line:1", "text": sample}],
                    }
                ],
            },
        )
        assert first_response.status_code == 200, first_response.text
        first_issue_id: str = first_response.json()["issue_ids"][0]
        await world.drain()

        env: PairEnv = PairEnv(
            (
                ("TELESCREEN_DATABASE_URL", "sqlite+aiosqlite:///:memory:"),
                ("TELESCREEN_FINGERPRINT_KEYS", "1=env:E2E_FP,2=env:E2E_FP_V2"),
                ("E2E_FP", "e2e-fingerprint-" + "x" * 40),
                ("E2E_FP_V2", "fingerprint-key-v2-" + "y" * 40),
                ("TELESCREEN_EVIDENCE_DIR", str(tmp_path / "evidence-v2")),
                ("TELESCREEN_GITHUB_API_URL", "https://github.example.invalid/api/v3"),
            )
        )
        second_http = httpx.AsyncClient(transport=world.outbound.transport())
        second_container = build_container(
            load_settings(env), env, second_http, world.database.sessions, world.clock
        )
        second_container.worker.concurrency = 1
        app: FastAPI = create_app(second_container)
        second_client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        second_response: httpx.Response = await second_client.post(
            "/api/ingest/v1/scan",
            headers=bearer(ingest.token),
            json={
                "source": "custom:v11-key-rotation",
                "resources": [
                    {
                        "locator": "second.txt",
                        "segments": [{"locator": "line:1", "text": sample}],
                    }
                ],
            },
        )
        assert second_response.status_code == 200, second_response.text
        second_issue_id: str = second_response.json()["issue_ids"][0]
        await second_container.worker.run_until_idle()
        occurrences: httpx.Response = await world.client.get(
            f"/api/issues/{second_issue_id}/occurrences",
            headers=bearer(world.admin_token),
        )
        assert occurrences.status_code == 200, occurrences.text
        assert first_issue_id not in occurrences.text

        repeated: httpx.Response = await second_client.post(
            "/api/ingest/v1/scan",
            headers=bearer(ingest.token),
            json={
                "source": "custom:v11-key-rotation",
                "resources": [
                    {
                        "locator": "first.txt",
                        "segments": [{"locator": "line:1", "text": sample}],
                    }
                ],
            },
        )
        assert repeated.status_code == 200, repeated.text
        await second_container.worker.run_until_idle()
        async with world.database.sessions() as session:
            issues: tuple[IssueRow, ...] = tuple(await session.scalars(select(IssueRow)))
            finding: FindingRow | None = await session.scalar(
                select(FindingRow)
                .where(FindingRow.issue_id == first_issue_id)
                .order_by(FindingRow.observed_at.desc())
            )
        assert len(issues) == 2
        assert finding is not None and finding.fingerprint_key_version == 2
        assert second_container.settings.fingerprint_active_version == 2
    finally:
        if second_client is not None:
            await second_client.aclose()
        if second_container is not None:
            await close_container(second_container)
        if second_http is not None:
            await second_http.aclose()
        await world.close()
