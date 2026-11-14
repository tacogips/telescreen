"""End-to-end GitHub credential response through the assembled application."""

import base64
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from telescreen_police_sdk.client import BureauClient
from telescreen_police_sdk.models import (
    ClaimRequest,
    Manifest,
    Mode,
    Outcome,
    Registration,
    ResultEnvelope,
    ResultStatus,
    TaskEnvelope,
)
from tests.e2e.harness import (
    E2EWorld,
    OutboundRecorder,
    admin_post,
    bearer,
    github_signature,
    make_world,
)
from tests.support.samples import (
    Sample,
    assert_no_leaks,
    aws_access_key_id,
    aws_secret_access_key,
    find_leaks,
)


@dataclass(frozen=True)
class TaskView:
    """Fields needed to drive an API-created human task."""

    id: str
    step_run_id: str
    assignee_ref: str
    kind: str


async def _get(world: E2EWorld, path: str) -> httpx.Response:
    """GET through the assembled API and retain its response for leak checks."""
    response: httpx.Response = await world.client.get(path, headers=bearer(world.admin_token))
    world.responses.append(response.text)
    return response


async def _task(world: E2EWorld, kind: str, assignee: str | None = None) -> TaskView:
    """Return the first open task with the requested kind and optional recipient."""
    response: httpx.Response = await _get(world, "/api/human-tasks?status=open&assignee=all")
    assert response.status_code == 200, response.text
    return next(
        TaskView(item["id"], item["step_run_id"], item["assignee_ref"], item["kind"])
        for item in response.json()["items"]
        if item["kind"] == kind and (assignee is None or item["assignee_ref"] == assignee)
    )


async def _complete(world: E2EWorld, task: TaskView, outcome: str) -> httpx.Response:
    """Complete one task through the public human-task endpoint."""
    response: httpx.Response = await admin_post(
        world,
        f"/api/human-tasks/{task.id}/complete",
        json.dumps({"outcome": outcome}),
    )
    return response


async def _register_police(world: E2EWorld) -> tuple[BureauClient, str, str]:
    """Enroll the pull-mode verifier using the published Police SDK."""
    client: BureauClient = BureauClient("http://test", world.client)
    registration: Registration = await client.register(
        Manifest(
            "e2e-github-police",
            "1.0",
            Mode.PULL,
            ("verify_remediation",),
            ("scope=github-enterprise",),
            1,
        ),
        "e2e-enrollment-" + "x" * 32,
    )
    assert registration.worker_token is not None
    return client, registration.worker_id, registration.worker_token


async def _police_result(
    client: BureauClient, worker_id: str, token: str, outcome: Outcome
) -> None:
    """Claim and settle exactly one verification task via the SDK client."""
    tasks: tuple[TaskEnvelope, ...] = await client.claim(
        worker_id, token, ClaimRequest(1, 0, ("verify_remediation",))
    )
    assert len(tasks) == 1
    await client.submit_result(
        token,
        ResultEnvelope(
            tasks[0].task_id,
            ResultStatus.COMPLETED,
            outcome=outcome,
            summary="Repository remediation check completed",
        ),
    )


@pytest.mark.asyncio
async def test_github_remediation_escalates_rejects_evidence_and_rechecks(tmp_path: Path) -> None:
    """Drive remediation through escalation, evidence, send-back and Police pass."""
    access_key: Sample = aws_access_key_id()
    secret_key: Sample = aws_secret_access_key()
    source_text: str = Path("examples/workflows/github-credential-leak.yaml").read_text()
    github_body: bytes = json.dumps(
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
    source_file: bytes = f"access_key={access_key.value}\nsecret_key={secret_key.value}\n".encode()

    def github(request: httpx.Request) -> httpx.Response:
        if "/contents/" in request.url.path:
            return httpx.Response(
                200,
                request=request,
                json={
                    "content": base64.b64encode(source_file).decode("ascii"),
                    "encoding": "base64",
                    "size": len(source_file),
                    "sha": "abcdef0123456789",
                },
            )
        return httpx.Response(200, request=request, json={"default_branch": "main"})

    def anthropic(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "msg_e2e",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "content": [
                    {
                        "type": "text",
                        "text": '{"is_live_credential":true,"summary":"masked summary"}',
                    }
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    def slack(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"ok": True})

    outbound: OutboundRecorder = OutboundRecorder(
        handlers=[
            ("github.example.invalid", github),
            ("api.anthropic.com", anthropic),
            ("slack.com", slack),
        ]
    )
    world: E2EWorld = await make_world(
        tmp_path,
        (
            ("TELESCREEN_GITHUB_WEBHOOK_SECRET", "env:E2E_GH_HOOK"),
            ("E2E_GH_HOOK", "e2e-hook-" + "h" * 32),
            ("E2E_GH_TOKEN", "e2e-github-" + "g" * 40),
            ("TELESCREEN_ANTHROPIC_API_KEY", "env:E2E_ANTHROPIC"),
            ("E2E_ANTHROPIC", "e2e-anthropic-" + "a" * 32),
            ("TELESCREEN_SLACK_BOT_TOKEN", "env:E2E_SLACK"),
            ("E2E_SLACK", "e2e-slack-" + "s" * 32),
            ("TELESCREEN_POLICE_ENROLLMENT_TOKEN", "env:E2E_ENROLL"),
            ("E2E_ENROLL", "e2e-enrollment-" + "x" * 32),
        ),
        outbound,
    )
    try:
        teams: tuple[str, ...] = (
            "secops-triage",
            "secops-tier2",
            "dev-remediation",
            "dev-remediation-leads",
            "github-enterprise-admins",
        )
        response: httpx.Response
        for slug in teams:
            response = await admin_post(
                world,
                "/api/teams",
                json.dumps(
                    {"slug": slug, "name": slug, "email": None, "slack_channel": f"#{slug}"}
                ),
            )
            assert response.status_code == 201, response.text
        for name, assign_to, notify, after in (
            ("secops-standard", "team:secops-tier2", ("team:secops-tier2",), "PT4H"),
            (
                "dev-remediation",
                "team:dev-remediation-leads",
                ("team:dev-remediation-leads",),
                "PT24H",
            ),
        ):
            policy_text: str = json.dumps(
                {
                    "name": name,
                    "tiers": [
                        {
                            "after": after,
                            "assign_to": assign_to,
                            "notify": notify,
                            "channels": ["slack"],
                        },
                        {
                            "after": "PT8H",
                            "assign_to": assign_to,
                            "notify": notify,
                            "channels": ["slack"],
                        },
                    ],
                }
            )
            response = await admin_post(
                world,
                "/api/escalation-policies",
                json.dumps({"tiers_json": policy_text, "source_format": "json"}),
            )
            assert response.status_code == 201, response.text

        response = await admin_post(
            world,
            "/api/workflows",
            json.dumps({"source_format": "yaml", "source_text": source_text}),
        )
        assert response.status_code == 201, response.text
        response = await admin_post(
            world,
            "/api/workflows/github-credential-leak/activate",
            json.dumps({"version": 1}),
        )
        assert response.status_code == 200, response.text
        response = await admin_post(
            world,
            "/api/collectors",
            json.dumps(
                {
                    "name": "e2e-github",
                    "kind": "github",
                    "trigger_mode": "event",
                    "cron": None,
                    "sweep_cron": None,
                    "selector_json": json.dumps(
                        {"owner": "acme", "repos": ["app"], "branches": ["main"]}
                        | {"path_globs": []}
                    ),
                    "credential_ref": "env:E2E_GH_TOKEN",
                    "enabled": True,
                }
            ),
        )
        assert response.status_code == 200, response.text
        config_id: str = response.json()["id"]
        hook: httpx.Response = await world.client.post(
            "/api/collectors/github/webhook",
            content=github_body,
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": github_signature("e2e-hook-" + "h" * 32, github_body),
            },
        )
        world.responses.append(hook.text)
        assert hook.status_code == 200, hook.text
        await world.drain()

        issues: httpx.Response = await _get(world, "/api/issues?source=github")
        assert len(issues.json()["items"]) == 1, issues.text
        issue_id: str = issues.json()["items"][0]["id"]
        runs: httpx.Response = await _get(world, f"/api/runs?issue_id={issue_id}")
        assert len(runs.json()["items"]) == 1, runs.text
        run_id: str = runs.json()["items"][0]["id"]
        run: httpx.Response = await _get(world, f"/api/runs/{run_id}")
        for step_id in ("rescan", "verify_credential", "live_check"):
            assert any(
                item["step_id"] == step_id and item["status"] == "succeeded"
                for item in run.json()["steps"]
            ), [
                (item["step_id"], item["status"], item.get("output_json"), item.get("error_code"))
                for item in run.json()["steps"]
            ]
        open_tasks: httpx.Response = await _get(world, "/api/human-tasks?status=open&assignee=all")
        assert open_tasks.json()["items"], open_tasks.text
        triage: TaskView = await _task(world, "approval")
        assert triage.assignee_ref == "team:secops-triage"
        world.clock.advance(timedelta(hours=4, minutes=1))
        await world.drain()
        escalated: TaskView = await _task(world, "approval")
        assert escalated.id == triage.id and escalated.assignee_ref == "team:secops-tier2"
        assert any(request.host == "slack.com" for request in outbound.requests)
        assert all(not find_leaks(request.body.decode("utf-8")) for request in outbound.requests)

        assert (await _complete(world, escalated, "remediation_required")).status_code == 200
        await world.drain()
        evidence_task: TaskView = await _task(world, "evidence")
        too_early: httpx.Response = await _complete(world, evidence_task, "completed")
        assert too_early.status_code == 409 and "evidence_required" in too_early.text
        rejected: httpx.Response = await world.client.put(
            f"/api/evidence?issue_id={issue_id}&step_run_id={evidence_task.step_run_id}&filename=proof.txt",
            content=f"proof {access_key.value}".encode(),
            headers=bearer(world.admin_token),
        )
        world.responses.append(rejected.text)
        assert rejected.status_code == 422
        assert "rule_classes" in rejected.text and access_key.value not in rejected.text
        assert not tuple((tmp_path / "evidence").glob("**/*"))
        police: tuple[BureauClient, str, str] = await _register_police(world)
        police_client: BureauClient = police[0]
        police_worker_id: str = police[1]
        police_worker_token: str = police[2]
        for cycle in range(2):
            evidence_task = await _task(world, "evidence")
            accepted: httpx.Response = await world.client.put(
                f"/api/evidence?issue_id={issue_id}&step_run_id={evidence_task.step_run_id}&filename=proof.txt",
                content=b"Credential removed from rewritten history.",
                headers=bearer(world.admin_token),
            )
            world.responses.append(accepted.text)
            assert accepted.status_code == 201, accepted.text
            assert (await _complete(world, evidence_task, "completed")).status_code == 200
            await world.drain()
            admin_task: TaskView = await _task(world, "approval", "team:github-enterprise-admins")
            assert (await _complete(world, admin_task, "verified")).status_code == 200
            await world.drain()
            if cycle == 0:
                await _police_result(
                    police_client, police_worker_id, police_worker_token, Outcome.FAILED
                )
                await world.drain()
                run = await _get(world, f"/api/runs/{run_id}")
                assert any(
                    item["kind"] == "send_back" and item["to_step_id"] == "dev_remediation"
                    for item in run.json()["transitions"]
                )
            else:
                await _police_result(
                    police_client, police_worker_id, police_worker_token, Outcome.PASSED
                )
                await world.drain()

        final_run: httpx.Response = await _get(world, f"/api/runs/{run_id}")
        assert final_run.json()["run"]["status"] == "succeeded"
        assert any(
            item["step_id"] == "completed" and item["status"] == "succeeded"
            for item in final_run.json()["steps"]
        )
        history: httpx.Response = await _get(world, f"/api/issues/{issue_id}/history")
        assert history.json()["items"]
        audit: httpx.Response = await _get(world, "/api/audit/verify")
        assert audit.json()["ok"] is True
        assert not outbound.failures
        for request in outbound.requests:
            assert_no_leaks(request.body.decode("utf-8"), "outbound request")
        for response_text in world.responses:
            assert_no_leaks(response_text, "API response")
        assert config_id
    finally:
        await world.close()
