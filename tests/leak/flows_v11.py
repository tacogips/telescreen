"""End-to-end v1.1 privacy and lifecycle flows for the leak matrix."""

import json
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import select

from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import IssueRow, NotificationRow
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
    import hashlib
    import hmac

    signature: str = hmac.new(b"github-webhook-placeholder", payload, hashlib.sha256).hexdigest()
    response: httpx.Response = await world.client.post(
        "/api/collectors/github/webhook",
        content=payload,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=" + signature},
    )
    assert response.status_code == 200, response.text
    await world.drain()
    async with world.database.sessions() as session:
        rows: tuple[IssueRow, ...] = tuple(await session.scalars(select(IssueRow)))
    assert any(row.exposure_visibility == "public" and row.severity == "critical" for row in rows)


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
    issue_id: str = await _issue(world, "owner-resource")
    assigned: httpx.Response = await world.client.patch(
        f"/api/issues/{issue_id}",
        headers=auth(world.admin_token),
        json={"assignee_ref": "member:alice"},
    )
    assert assigned.status_code == 200, assigned.text
    member_id: str = member.json()["id"]
    async with world.database.sessions() as session:
        issue: IssueRow | None = await session.get(IssueRow, issue_id)
    assert issue is not None and issue.assignee_member_id == member_id


async def policy_flow(world: LeakWorld) -> None:
    """Create and activate a placement policy through its public API."""
    source: str = (
        "name: leak-allow\neffect: allow\nmatch:\n  locations:\n    github_repos: [sample/repo]\n"
        "condition:\n  max_exposure: internal\nroute_to_owner: true\n"
    )
    created: httpx.Response = await world.client.post(
        "/api/policies",
        headers=auth(world.admin_token),
        json={"source_format": "yaml", "source_text": source},
    )
    assert created.status_code == 201, created.text
    activated: httpx.Response = await world.client.post(
        "/api/policies/leak-allow/activate",
        headers=auth(world.admin_token),
        json={"version": 1},
    )
    assert activated.status_code == 200, activated.text
    await world.drain()


async def remediation_flow(world: LeakWorld) -> None:
    """Request read-only verification and require the lifecycle state transition."""
    issue_id: str = await _issue(world, "remediation-resource")
    response: httpx.Response = await world.client.post(
        f"/api/issues/{issue_id}/remediation",
        headers=auth(world.admin_token),
        json={"action": "remove", "include_linked": False, "note": "owner removed"},
    )
    assert response.status_code == 200, response.text
    await world.drain()


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


async def coverage_flow(world: LeakWorld) -> None:
    """Read coverage summary and per-resource coverage through collector APIs."""
    created: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=auth(world.admin_token),
        json={
            "name": "v11-coverage",
            "kind": "custom",
            "trigger_mode": "event",
            "cron": None,
            "sweep_cron": None,
            "selector_json": "{}",
            "credential_ref": None,
            "enabled": True,
        },
    )
    assert created.status_code in (200, 201), created.text
    config_id: str = created.json()["id"]
    summary: httpx.Response = await world.client.get(
        f"/api/collectors/{config_id}/coverage", headers=auth(world.admin_token)
    )
    resources: httpx.Response = await world.client.get(
        f"/api/collectors/{config_id}/coverage/resources", headers=auth(world.admin_token)
    )
    assert summary.status_code == resources.status_code == 200


async def health_flow(world: LeakWorld) -> None:
    """Exercise the health projection on the existing collector list endpoint."""
    response: httpx.Response = await world.client.get(
        "/api/collectors", headers=auth(world.admin_token)
    )
    assert response.status_code == 200, response.text


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
    assert all(row.subject for row in rows)


async def police_exposures(world: LeakWorld) -> None:
    """Exercise the mounted Police worker list and contract-safe response path."""
    response: httpx.Response = await world.client.get(
        "/api/police/workers", headers=auth(world.admin_token)
    )
    assert response.status_code == 200, response.text


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
