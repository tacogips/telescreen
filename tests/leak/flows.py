"""End-to-end leak-matrix flows through the assembled HTTP application."""

import hashlib
import hmac
import json
import logging
import sys
from datetime import datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from telescreen.db.models import PoliceTaskRow
from telescreen.masking.log_masking import install_excepthook
from tests.leak.harness import LeakWorld, auth
from tests.leak.images import jpeg_with_metadata, png_with_metadata
from tests.support.samples import all_samples, github_token


async def push_ingestion(world: LeakWorld) -> tuple[str, ...]:
    """Scan all runtime samples and reject an externally pushed raw finding."""
    config: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=auth(world.admin_token),
        json={
            "name": "demo",
            "kind": "custom",
            "trigger_mode": "event",
            "cron": None,
            "sweep_cron": None,
            "selector_json": "{}",
            "credential_ref": None,
            "enabled": True,
        },
    )
    assert config.status_code == 200, config.status_code
    response: httpx.Response = await world.client.post(
        "/api/ingest/v1/scan",
        headers=auth(world.ingest_token),
        json={
            "source": "custom:demo",
            "resources": [
                {
                    "locator": f"sample-{sample.name}",
                    "segments": [{"locator": "line:1", "text": sample.value}],
                }
                for sample in all_samples()
            ],
        },
    )
    assert response.status_code == 200, response.status_code
    ids: tuple[str, ...] = tuple(response.json()["issue_ids"])
    raw: str = github_token().value
    rejected: httpx.Response = await world.client.post(
        "/api/ingest/v1/findings",
        headers=auth(world.ingest_token),
        json={
            "source": "custom:demo",
            "locator": "raw-finding",
            "findings": [
                {
                    "locator": "raw-finding",
                    "rule_id": "token.github",
                    "rule_class": "token",
                    "masked_snippet": raw,
                    "location": "line:1",
                    "confidence": 1.0,
                    "external_fingerprint": "external-fingerprint",
                }
            ],
        },
    )
    assert rejected.status_code == 422
    return ids


async def collector_scan(world: LeakWorld) -> None:
    """Create a GitHub collector and enqueue a correctly signed push webhook."""
    secret: bytes = b"github-webhook-placeholder"
    payload: bytes = json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {
                "full_name": "sample/repo",
                "owner": {"login": "sample"},
                "name": "repo",
                "default_branch": "main",
            },
            "commits": [{"added": [], "modified": ["secrets.txt"], "removed": []}],
        }
    ).encode()
    signature: str = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    created: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=auth(world.admin_token),
        json={
            "name": "leak-github",
            "kind": "github",
            "trigger_mode": "event",
            "cron": None,
            "sweep_cron": None,
            "selector_json": json.dumps(
                {
                    "owner": "sample",
                    "repos": ["repo"],
                    "branches": ["main"],
                    "path_globs": [],
                }
            ),
            "credential_ref": "env:TELESCREEN_GITHUB_TOKEN",
            "enabled": True,
        },
    )
    assert created.status_code in (200, 201), created.text
    response: httpx.Response = await world.client.post(
        "/api/collectors/github/webhook",
        content=payload,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": f"sha256={signature}"},
    )
    assert response.status_code == 200
    await world.drain()
    assert any("/contents/secrets.txt" in url for url, _ in world.recorder.requests)


async def issue_and_search_reads(world: LeakWorld, issue_ids: tuple[str, ...]) -> None:
    """Read issue details, history, search and audit using masked and raw queries."""
    listed: httpx.Response = await world.client.get("/api/issues", headers=auth(world.admin_token))
    assert listed.status_code == 200
    for issue_id in issue_ids:
        detail: httpx.Response = await world.client.get(
            f"/api/issues/{issue_id}", headers=auth(world.admin_token)
        )
        assert detail.status_code == 200
        for suffix in ("events", "history"):
            item: httpx.Response = await world.client.get(
                f"/api/issues/{issue_id}/{suffix}", headers=auth(world.admin_token)
            )
            assert item.status_code == 200
    for query in ("ghp_********", github_token().value):
        result: httpx.Response = await world.client.get(
            "/api/search", params={"q": query}, headers=auth(world.admin_token)
        )
        assert result.status_code == 200
    audit: httpx.Response = await world.client.get(
        "/api/audit/search",
        params={"q": github_token().value},
        headers=auth(world.admin_token),
    )
    assert audit.status_code == 200


async def notify_workflow(world: LeakWorld) -> None:
    """Trigger a Slack notification from an issue-created workflow."""
    team: httpx.Response = await world.client.post(
        "/api/teams",
        headers=auth(world.admin_token),
        json={"slug": "leak-team", "name": "Leak Team", "slack_channel": "C-LEAK"},
    )
    assert team.status_code == 201
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: leak-notify
start: notify
trigger:
  "on": [issue_created]
  match:
    sources: ["custom:demo"]
steps:
  - id: notify
    type: notify
    channel: slack
    to: [team:leak-team]
    subject: "Telescreen issue"
    body: "Issue {{ issue.title }}"
    next: end
  - id: end
    type: end
    result: succeeded
"""
    uploaded: httpx.Response = await world.client.post(
        "/api/workflows",
        headers=auth(world.admin_token),
        json={"source_format": "yaml", "source_text": source},
    )
    assert uploaded.status_code == 201, uploaded.text
    version: int = uploaded.json()["version"]
    activated: httpx.Response = await world.client.post(
        "/api/workflows/leak-notify/activate",
        headers=auth(world.admin_token),
        json={"version": version},
    )
    assert activated.status_code == 200
    triggered: httpx.Response = await world.client.post(
        "/api/ingest/v1/scan",
        headers=auth(world.ingest_token),
        json={
            "source": "custom:demo",
            "resources": [
                {
                    "locator": "workflow-notification-resource",
                    "segments": [{"locator": "line:1", "text": github_token().value}],
                }
            ],
        },
    )
    assert triggered.status_code == 200
    await world.drain()


async def text_evidence(world: LeakWorld, issue_id: str) -> None:
    """Reject sensitive text evidence, then accept safe text evidence."""
    sample: str = github_token().value
    rejected: httpx.Response = await world.client.put(
        "/api/evidence",
        params={"issue_id": issue_id, "filename": "raw.txt"},
        headers=auth(world.admin_token),
        content=sample.encode(),
    )
    assert rejected.status_code == 422
    accepted: httpx.Response = await world.client.put(
        "/api/evidence",
        params={"issue_id": issue_id, "filename": "clean.txt"},
        headers=auth(world.admin_token),
        content=b"remediation complete",
    )
    assert accepted.status_code == 201


async def image_evidence(world: LeakWorld, issue_id: str) -> None:
    """Exercise image rejection, clean metadata stripping and scanner failure output."""
    sample: str = github_token().value
    world.recorder.llm_responses.extend(
        (
            '{"contains_sensitive":true,"rule_classes":["token"]}',
            '{"contains_sensitive":false,"rule_classes":[]}',
            '{"contains_sensitive":false,"rule_classes":[]}',
            "free text output",
        )
    )
    image: bytes = png_with_metadata(sample)
    rejected: httpx.Response = await world.client.put(
        "/api/evidence",
        params={"issue_id": issue_id, "filename": "sensitive.png"},
        headers=auth(world.admin_token),
        content=image,
    )
    assert rejected.status_code == 422
    for name, data in (("safe.png", image), ("safe.jpg", jpeg_with_metadata(sample))):
        accepted: httpx.Response = await world.client.put(
            "/api/evidence",
            params={"issue_id": issue_id, "filename": name},
            headers=auth(world.admin_token),
            content=data,
        )
        assert accepted.status_code == 201
    free_text: httpx.Response = await world.client.put(
        "/api/evidence",
        params={"issue_id": issue_id, "filename": "free.png"},
        headers=auth(world.admin_token),
        content=image,
    )
    assert free_text.status_code == 422


async def scanner_unavailable(world: LeakWorld, issue_id: str) -> None:
    """Require fail-closed 503 for image evidence when no scanner is configured."""
    result: httpx.Response = await world.client.put(
        "/api/evidence",
        params={"issue_id": issue_id, "filename": "image.png"},
        headers=auth(world.admin_token),
        content=png_with_metadata(github_token().value),
    )
    assert result.status_code == 503
    assert "evidence_scanner_unavailable" in result.text


async def police_results(world: LeakWorld) -> None:
    """Register, claim, reject a raw result, and accept a safe result."""
    registration: httpx.Response = await world.client.post(
        "/api/police/v1/workers/register",
        headers={"Authorization": "Bearer police-enrollment-placeholder"},
        json={
            "name": "leak-police",
            "version": "1.0",
            "contract_version": "1.0",
            "mode": "pull",
            "task_types": ["verify_remediation"],
            "labels": [],
            "concurrency": 1,
        },
    )
    assert registration.status_code == 201
    token: str = registration.json()["worker_token"]
    assert token
    now: datetime = world.clock.now()
    async with world.database.sessions.begin() as session:
        session.add(
            PoliceTaskRow(
                id="leak-police-task",
                task_type="verify_remediation",
                labels="",
                input_json="{}",
                status="queued",
                worker_id=None,
                attempt=0,
                max_attempts=3,
                deadline=now + timedelta(hours=1),
                created_at=now,
            )
        )
    worker_id: str = registration.json()["worker_id"]
    claim: httpx.Response = await world.client.post(
        f"/api/police/v1/workers/{worker_id}/claim",
        headers={"Authorization": f"Bearer {token}"},
        json={"max_tasks": 1, "wait_seconds": 0, "task_types": ["verify_remediation"]},
    )
    assert claim.status_code == 200
    task_id: str = claim.json()["tasks"][0]["task_id"]
    rejected: httpx.Response = await world.client.post(
        f"/api/police/v1/tasks/{task_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "contract_version": "1.0",
            "task_id": task_id,
            "status": "completed",
            "outcome": "passed",
            "summary": github_token().value,
        },
    )
    assert rejected.status_code == 422
    accepted: httpx.Response = await world.client.post(
        f"/api/police/v1/tasks/{task_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "contract_version": "1.0",
            "task_id": task_id,
            "status": "completed",
            "outcome": "passed",
        },
    )
    assert accepted.status_code == 204


async def chat(world: LeakWorld) -> None:
    """Send a sensitive user message and receive a canned chat answer."""
    world.recorder.llm_responses.extend(
        (
            '{"action":"tool","tool":"list_workflow_runs","arguments":{"limit":1}}',
            '{"action":"answer","answer":"No raw values stored."}',
        )
    )
    response: httpx.Response = await world.client.post(
        "/api/chat",
        headers=auth(world.admin_token),
        json={"messages": [{"role": "user", "content": github_token().value}]},
    )
    assert response.status_code == 200


async def forced_exception(
    world: LeakWorld,
    sample: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[str, str, str]:
    """Capture an API traceback and the installed production excepthook output."""

    async def boom() -> None:
        raise RuntimeError(sample)

    world.app.add_api_route("/api/leak-boom", boom)
    async with AsyncClient(
        transport=ASGITransport(app=world.app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response: httpx.Response = await client.get("/api/leak-boom")
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    install_excepthook(world.container.redactor)
    try:
        raise RuntimeError(sample)
    except RuntimeError as error:
        sys.excepthook(type(error), error, error.__traceback__)
    logging.getLogger("telescreen.leaktest").exception("boom %s", sample)
    return response.text, world.log_buffer.getvalue(), capsys.readouterr().err
