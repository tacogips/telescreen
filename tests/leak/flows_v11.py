"""End-to-end v1.1 privacy and lifecycle flows for the leak matrix."""

import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import select

from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import (
    IssueExceptionRow,
    IssueHistoryRow,
    IssueRow,
    NotificationRow,
    ResourceCoverageRow,
)
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.leak.harness import CANARY_EMAIL, CANARY_FILENAME, LeakWorld, auth
from tests.support.samples import github_token


async def _custom_collector(world: LeakWorld) -> str:
    """Create one test custom collector and return its source name."""
    name: str = "v11-leak"
    response: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=auth(world.admin_token),
        json={
            "name": name,
            "kind": "custom",
            "trigger_mode": "event",
            "cron": None,
            "sweep_cron": None,
            "selector_json": "{}",
            "credential_ref": None,
            "enabled": True,
        },
    )
    assert response.status_code in (200, 201), response.text
    return name


async def _issue(world: LeakWorld, locator: str = "safe-resource.txt") -> str:
    """Ingest a runtime sample and return its issue id."""
    response: httpx.Response = await world.client.post(
        "/api/ingest/v1/scan",
        headers=auth(world.ingest_token),
        json={
            "source": "custom:v11-leak",
            "resources": [
                {
                    "locator": locator,
                    "title": "safe title",
                    "segments": [{"locator": "line:1", "text": github_token().value}],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    ids: list[str] = response.json()["issue_ids"]
    assert ids
    return ids[0]


async def github_public_push(world: LeakWorld) -> None:
    """Scan a public GitHub file whose path contains the runtime filename canary."""
    selector: str = json.dumps(
        {"owner": "sample", "repos": ["repo"], "branches": ["main"], "path_globs": ["**"]}
    )
    created: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=auth(world.admin_token),
        json={
            "name": "v11-github-public",
            "kind": "github",
            "trigger_mode": "event",
            "cron": None,
            "sweep_cron": None,
            "selector_json": selector,
            "credential_ref": "env:TELESCREEN_GITHUB_TOKEN",
            "enabled": True,
        },
    )
    assert created.status_code in (200, 201), created.text
    payload: bytes = json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {
                "full_name": "sample/repo",
                "owner": {"login": "sample"},
                "name": "repo",
                "default_branch": "main",
                "visibility": "public",
                "private": False,
                "fork": False,
            },
            "head_commit": {
                "author": {"username": "canary-login"},
                "timestamp": "2026-10-10T00:00:00Z",
            },
            "commits": [
                {
                    "added": ["secrets/" + CANARY_FILENAME, "secrets/binary.bin"],
                    "modified": [],
                    "removed": [],
                }
            ],
        }
    ).encode()
    await _send_github_push(world, payload)
    async with world.database.sessions() as session:
        rows: tuple[IssueRow, ...] = tuple(await session.scalars(select(IssueRow)))
    assert any(row.exposure_visibility == "public" and row.severity == "critical" for row in rows)


async def _push_file(world: LeakWorld, path: str) -> None:
    """Trigger a GitHub scan for one added path in sample/repo."""
    payload: bytes = json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {
                "full_name": "sample/repo",
                "owner": {"login": "sample"},
                "name": "repo",
                "default_branch": "main",
                "visibility": "public",
                "private": False,
                "fork": False,
            },
            "head_commit": {
                "author": {"username": "canary-login"},
                "timestamp": "2026-10-10T00:00:00Z",
            },
            "commits": [{"added": [path], "modified": [], "removed": []}],
        }
    ).encode()
    await _send_github_push(world, payload)


async def _github_issue(world: LeakWorld, filename: str) -> IssueRow:
    """Return the GitHub issue row for a just-scanned test path."""
    async with world.database.sessions() as session:
        issue: IssueRow | None = await session.scalar(
            select(IssueRow).where(IssueRow.display_locator.endswith(filename))
        )
    assert issue is not None
    return issue


async def _send_github_push(world: LeakWorld, payload: bytes) -> None:
    """Send one signed sample/repo push through the configured collector."""
    signature: str = hmac.new(b"github-webhook-placeholder", payload, hashlib.sha256).hexdigest()
    response: httpx.Response = await world.client.post(
        "/api/collectors/github/webhook",
        content=payload,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=" + signature},
    )
    assert response.status_code == 200, response.text
    await world.drain()
    return


async def owner_mapping(world: LeakWorld) -> None:
    """Route a keyed canary login identity to an explicitly assigned member."""
    member: httpx.Response = await world.client.post(
        "/api/members",
        headers=auth(world.admin_token),
        json={"handle": "alice", "display_name": "Alice", "email": CANARY_EMAIL},
    )
    assert member.status_code == 201, member.text
    identity: httpx.Response = await world.client.post(
        "/api/members/alice/identities",
        headers=auth(world.admin_token),
        json={"kind": "github_login", "value": "canary-login"},
    )
    assert identity.status_code == 201, identity.text
    await _push_file(world, "secrets/owner-file.txt")
    member_id: str = member.json()["id"]
    issue: IssueRow = await _github_issue(world, "owner-file.txt")
    assert issue.assignee_member_id == member_id


async def policy_flow(world: LeakWorld) -> None:
    """Assert policy allow, disable reopening, and violate-wins lifecycle behavior."""
    async with world.database.sessions() as session:
        issue: IssueRow | None = await session.scalar(
            select(IssueRow).where(
                IssueRow.source == "github", IssueRow.display_locator.like("%" + CANARY_FILENAME)
            )
        )
    assert issue is not None
    allow_text: str = (
        "name: leak-allow\neffect: allow\nmatch:\n  locations:\n    github_repos: [sample/repo]\n"
        "route_to_owner: true\n"
    )
    violate_text: str = (
        "name: leak-unmatched\neffect: violate\nmatch:\n  locations:\n"
        "    github_repos: [sample/other-repo]\n"
    )
    for name, text in (("leak-allow", allow_text), ("leak-unmatched", violate_text)):
        created: httpx.Response = await world.client.post(
            "/api/policies",
            headers=auth(world.admin_token),
            json={"source_format": "yaml", "source_text": text},
        )
        assert created.status_code == 201, created.text
        activated: httpx.Response = await world.client.post(
            f"/api/policies/{name}/activate", headers=auth(world.admin_token), json={"version": 1}
        )
        assert activated.status_code == 200, activated.text
    await world.drain()
    async with world.database.sessions() as session:
        allowed: IssueRow | None = await session.get(IssueRow, issue.id)
        history: tuple[IssueHistoryRow, ...] = tuple(
            await session.scalars(
                select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue.id)
            )
        )
    assert allowed is not None and (allowed.status, allowed.resolution) == (
        "allowed",
        "policy_changed",
    )
    assert any(row.kind == "policy_reevaluated" for row in history)
    disabled: httpx.Response = await world.client.post(
        "/api/policies/leak-allow/disable", headers=auth(world.admin_token), json={}
    )
    assert disabled.status_code == 200, disabled.text
    await world.drain()
    async with world.database.sessions() as session:
        reopened: IssueRow | None = await session.get(IssueRow, issue.id)
        history = tuple(
            await session.scalars(
                select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue.id)
            )
        )
    assert reopened is not None and (reopened.status, reopened.resolution) == ("unresolved", None)
    assert any(row.from_value == "allowed" and row.to_value == "unresolved" for row in history)
    violate: httpx.Response = await world.client.post(
        "/api/policies/leak-allow/activate", headers=auth(world.admin_token), json={"version": 1}
    )
    assert violate.status_code == 200, violate.text
    matching_text: str = (
        "name: leak-violate\neffect: violate\nmatch:\n  locations:\n"
        "    github_repos: [sample/repo]\n"
    )
    matching: httpx.Response = await world.client.post(
        "/api/policies",
        headers=auth(world.admin_token),
        json={"source_format": "yaml", "source_text": matching_text},
    )
    assert matching.status_code == 201, matching.text
    active: httpx.Response = await world.client.post(
        "/api/policies/leak-violate/activate", headers=auth(world.admin_token), json={"version": 1}
    )
    assert active.status_code == 200, active.text
    await world.drain()
    async with world.database.sessions() as session:
        final: IssueRow | None = await session.get(IssueRow, issue.id)
    assert final is not None and final.status == "unresolved"


async def remediation_flow(world: LeakWorld) -> None:
    """Verify clean content and 404 GitHub resources produce distinct outcomes."""
    filename: str = "remediation-clean.txt"
    clean_path: str = "/repos/sample/repo/contents/secrets/" + filename
    await _push_file(world, "secrets/" + filename)
    issue_id: str = (await _github_issue(world, filename)).id
    world.recorder.clean_content_paths.add(clean_path)
    response: httpx.Response = await world.client.post(
        f"/api/issues/{issue_id}/remediation",
        headers=auth(world.admin_token),
        json={"action": "remove", "include_linked": False, "note": "owner removed"},
    )
    assert response.status_code == 200, response.text
    async with world.database.sessions() as session:
        pending: IssueRow | None = await session.get(IssueRow, issue_id)
    assert pending is not None and pending.status == "pending_verification"
    await world.drain()
    async with world.database.sessions() as session:
        verified: IssueRow | None = await session.get(IssueRow, issue_id)
    assert verified is not None and verified.resolution == "verified_removed"
    filename = "remediation-deleted.txt"
    missing_path: str = "/repos/sample/repo/contents/secrets/" + filename
    await _push_file(world, "secrets/" + filename)
    deleted_id: str = (await _github_issue(world, filename)).id
    world.recorder.missing_content_paths.add(missing_path)
    response = await world.client.post(
        f"/api/issues/{deleted_id}/remediation",
        headers=auth(world.admin_token),
        json={"action": "remove", "include_linked": False, "note": "owner removed"},
    )
    assert response.status_code == 200, response.text
    await world.drain()
    async with world.database.sessions() as session:
        deleted: IssueRow | None = await session.get(IssueRow, deleted_id)
    assert deleted is not None and (deleted.status, deleted.resolution) == (
        "resolved",
        "resource_deleted",
    )


async def exception_flow(world: LeakWorld) -> None:
    """Request, approve, and expire an exception using the API and fake clock."""
    issue_id: str = await _issue(world, "exception-resource")
    member: httpx.Response = await world.client.post(
        "/api/members",
        headers=auth(world.admin_token),
        json={"handle": "exception-owner", "display_name": "Exception Owner"},
    )
    assert member.status_code == 201, member.text
    member_id: str = member.json()["id"]
    async with world.database.sessions.begin() as session:
        member_token: CreatedToken = await TokenService(world.container.audit, world.clock).create(
            session,
            "v11-exception-member",
            frozenset((ApiScope.READ, ApiScope.WRITE)),
            member_id,
        )
    requested: httpx.Response = await world.client.post(
        f"/api/issues/{issue_id}/exceptions",
        headers=auth(member_token.token),
        json={"reason": "approved test exception", "duration": "7d"},
    )
    assert requested.status_code == 201, requested.text
    exception_id: str = requested.json()["id"]
    approved: httpx.Response = await world.client.post(
        f"/api/exceptions/{exception_id}/approve",
        headers=auth(world.admin_token),
        json={"note": "approved"},
    )
    assert approved.status_code == 200, approved.text
    world.clock.advance(timedelta(days=8))
    await world.container.worker.scheduler.tick()
    await world.drain()
    async with world.database.sessions() as session:
        exception: IssueExceptionRow | None = await session.get(IssueExceptionRow, exception_id)
        issue: IssueRow | None = await session.get(IssueRow, issue_id)
        history: tuple[IssueHistoryRow, ...] = tuple(
            await session.scalars(
                select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue_id)
            )
        )
    assert exception is not None and exception.status == "expired"
    assert issue is not None and issue.status == "unresolved"
    assert any(row.kind == "exception_expired" for row in history)


async def coverage_flow(world: LeakWorld) -> None:
    """Read coverage summary and per-resource coverage through collector APIs."""
    async with world.database.sessions() as session:
        row: ResourceCoverageRow | None = await session.scalar(
            select(ResourceCoverageRow).where(ResourceCoverageRow.reason == "binary")
        )
    assert row is not None
    config_id: str = row.collector_config_id
    summary: httpx.Response = await world.client.get(
        f"/api/collectors/{config_id}/coverage", headers=auth(world.admin_token)
    )
    resources: httpx.Response = await world.client.get(
        f"/api/collectors/{config_id}/coverage/resources", headers=auth(world.admin_token)
    )
    assert summary.status_code == resources.status_code == 200
    assert any(item["reason"] == "binary" for item in resources.json()["items"])


async def health_flow(world: LeakWorld) -> None:
    """Fail one dedicated collector three times and assert its team alert."""
    created: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=auth(world.admin_token),
        json={
            "name": "v11-health-failure",
            "kind": "github",
            "trigger_mode": "event",
            "cron": None,
            "sweep_cron": None,
            "selector_json": json.dumps(
                {"owner": "sample", "repos": ["repo"], "branches": ["main"], "path_globs": ["**"]}
            ),
            "credential_ref": "env:TELESCREEN_GITHUB_TOKEN",
            "enabled": True,
        },
    )
    assert created.status_code in (200, 201), created.text
    world.recorder.failing_hosts.add("api.github.com")
    for _ in range(3):
        run: httpx.Response = await world.client.post(
            f"/api/collectors/{created.json()['id']}/run", headers=auth(world.admin_token)
        )
        assert run.status_code == 200, run.text
        await world.drain()
    world.recorder.failing_hosts.clear()
    world.clock.advance(timedelta(minutes=1))
    await world.container.worker.scheduler.tick()
    await world.drain()
    async with world.database.sessions() as session:
        notifications: tuple[NotificationRow, ...] = tuple(
            await session.scalars(select(NotificationRow))
        )
    assert any(
        row.subject.startswith("Telescreen health: v11-health-failure")
        and row.recipient_ref == "team:security"
        for row in notifications
    )


async def digest_flow(world: LeakWorld) -> None:
    """Tick due work after assigning an issue to a member and inspect digest rows."""
    issue_id: str = await _issue(world, "digest-resource")
    assigned: httpx.Response = await world.client.patch(
        f"/api/issues/{issue_id}",
        headers=auth(world.admin_token),
        json={"assignee_ref": "member:alice"},
    )
    assert assigned.status_code == 200, assigned.text
    world.clock.advance(timedelta(minutes=2))
    await world.container.worker.scheduler.tick()
    await world.drain()
    async with world.database.sessions() as session:
        rows: tuple[NotificationRow, ...] = tuple(await session.scalars(select(NotificationRow)))
    assert any(
        row.subject == "Telescreen owner digest" and row.recipient_ref == "member:alice"
        for row in rows
    )


async def police_exposures(world: LeakWorld) -> None:
    """Submit a contract 1.1 detect result through a pull worker and assert exposure."""
    registered: httpx.Response = await world.client.post(
        "/api/police/v1/workers/register",
        headers=auth("police-enrollment-placeholder"),
        json={
            "contract_version": "1.1",
            "name": "leak-v11-worker",
            "version": "1.0",
            "mode": "pull",
            "task_types": ["detect"],
            "labels": [],
            "concurrency": 1,
        },
    )
    assert registered.status_code == 201, registered.text
    worker_id: str = registered.json()["worker_id"]
    worker_token: str = registered.json()["worker_token"]
    collector: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=auth(world.admin_token),
        json={
            "name": "v11-police-detect",
            "kind": "police",
            "trigger_mode": "schedule",
            "cron": "* * * * *",
            "sweep_cron": None,
            "selector_json": json.dumps({"labels": [], "input": {"scope": "private"}}),
            "credential_ref": None,
            "enabled": True,
        },
    )
    assert collector.status_code in (200, 201), collector.text
    world.clock.advance(timedelta(minutes=1))
    await world.container.worker.scheduler.tick()
    claim: httpx.Response = await world.client.post(
        f"/api/police/v1/workers/{worker_id}/claim",
        headers=auth(worker_token),
        json={"max_tasks": 1, "wait_seconds": 0, "task_types": ["detect"]},
    )
    assert claim.status_code == 200, claim.text
    assert len(claim.json()["tasks"]) == 1, claim.text
    task_id: str = claim.json()["tasks"][0]["task_id"]
    result: httpx.Response = await world.client.post(
        f"/api/police/v1/tasks/{task_id}/result",
        headers=auth(worker_token),
        json={
            "contract_version": "1.1",
            "task_id": task_id,
            "status": "completed",
            "findings": [
                {
                    "locator": "repo/police-result",
                    "rule_id": "credential",
                    "rule_class": "credential",
                    "masked_snippet": "[masked]",
                    "location": "file",
                    "confidence": 1,
                    "external_fingerprint": "police-fingerprint-0001",
                }
            ],
            "complete_locators": [],
            "resource_exposures": [{"locator": "repo/police-result", "visibility": "public"}],
        },
    )
    assert result.status_code in (200, 204), result.text
    async with world.database.sessions() as session:
        issues: tuple[IssueRow, ...] = tuple(
            await session.scalars(select(IssueRow).where(IssueRow.source.startswith("police:")))
        )
    assert any(issue.exposure_visibility == "public" for issue in issues)


async def occurrences_flow(world: LeakWorld) -> None:
    """Create linked token findings and read safe occurrence projections."""
    first: str = await _issue(world, "occurrences-one")
    second: str = await _issue(world, "occurrences-two")
    response: httpx.Response = await world.client.get(
        f"/api/issues/{first}/occurrences", headers=auth(world.admin_token)
    )
    assert response.status_code == 200, response.text
    assert any(item["issue_id"] == second for item in response.json()["items"])


async def run_v11_flows(world: LeakWorld) -> None:
    """Run every v1.1 flow against one real-container world."""
    await _custom_collector(world)
    await github_public_push(world)
    await owner_mapping(world)
    await policy_flow(world)
    await remediation_flow(world)
    await exception_flow(world)
    await coverage_flow(world)
    await health_flow(world)
    await digest_flow(world)
    await police_exposures(world)
    await occurrences_flow(world)
    await world.container.worker.scheduler.tick()
    await world.drain()


def example_workflow_source() -> str:
    """Read the owner-review example workflow text for functional E2E coverage."""
    return Path("examples/workflows/owner-review.yaml").read_text()
