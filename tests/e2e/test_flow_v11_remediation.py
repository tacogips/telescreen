"""End-to-end v1.1 linked remediation and verification request flow."""

import base64
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import IssueHistoryRow, IssueRow
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.e2e.harness import (
    E2EWorld,
    OutboundRecorder,
    admin_post,
    bearer,
    github_signature,
    make_world,
)
from tests.support.samples import Sample, aws_access_key_id, github_token


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


@pytest.mark.asyncio
async def test_binary_verification_rescan_is_inconclusive(tmp_path: Path) -> None:
    """A binary GitHub rescan leaves remediation pending and records its coverage state."""
    sample: Sample = aws_access_key_id()
    binary_mode: bool = False
    webhook_secret: str = "e2e-hook-" + "h" * 32
    body: bytes = json.dumps(
        {
            "ref": "refs/heads/main",
            "forced": False,
            "repository": {
                "id": 5,
                "name": "app",
                "default_branch": "main",
                "owner": {"login": "acme"},
            },
            "commits": [{"added": ["config/settings.py"], "modified": [], "removed": []}],
        }
    ).encode()

    def github(request: httpx.Request) -> httpx.Response:
        if "/contents/" in request.url.path:
            content: bytes = b"\x00\xff" if binary_mode else f"access_key={sample.value}\n".encode()
            return httpx.Response(
                200,
                request=request,
                json={
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                    "size": len(content),
                    "sha": "abcdef0123456789",
                },
            )
        return httpx.Response(
            200,
            request=request,
            json={"default_branch": "main", "visibility": "private", "private": True},
        )

    outbound: OutboundRecorder = OutboundRecorder(handlers=[("github.example.invalid", github)])
    world: E2EWorld = await make_world(
        tmp_path,
        (
            ("TELESCREEN_GITHUB_WEBHOOK_SECRET", "env:E2E_GH_HOOK"),
            ("E2E_GH_HOOK", webhook_secret),
            ("E2E_GH_TOKEN", "e2e-github-" + "g" * 40),
        ),
        outbound,
    )
    try:
        collector: httpx.Response = await admin_post(
            world,
            "/api/collectors",
            json.dumps(
                {
                    "name": "binary-verify",
                    "kind": "github",
                    "trigger_mode": "event",
                    "cron": None,
                    "sweep_cron": None,
                    "selector_json": json.dumps(
                        {
                            "owner": "acme",
                            "repos": ["app"],
                            "branches": ["main"],
                            "path_globs": ["**"],
                        }
                    ),
                    "credential_ref": "env:E2E_GH_TOKEN",
                    "enabled": True,
                }
            ),
        )
        assert collector.status_code == 200, collector.text
        hook: httpx.Response = await world.client.post(
            "/api/collectors/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": github_signature(webhook_secret, body),
            },
        )
        assert hook.status_code == 200, hook.text
        await world.drain()
        issues: httpx.Response = await world.client.get(
            "/api/issues?source=github", headers=bearer(world.admin_token)
        )
        assert issues.status_code == 200, issues.text
        issue_id: str = issues.json()["items"][0]["id"]
        binary_mode = True
        requested: httpx.Response = await admin_post(
            world,
            f"/api/issues/{issue_id}/remediation",
            json.dumps({"action": "remove", "include_linked": False}),
        )
        assert requested.status_code == 200, requested.text
        await world.drain()
        async with world.database.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            history: tuple[IssueHistoryRow, ...] = tuple(
                await session.scalars(
                    select(IssueHistoryRow).where(
                        IssueHistoryRow.issue_id == issue_id,
                        IssueHistoryRow.kind == "verification_inconclusive",
                    )
                )
            )
        assert issue is not None and issue.status == "pending_verification"
        assert len(history) == 1
        assert history[0].to_value == "unsupported"
    finally:
        await world.close()
