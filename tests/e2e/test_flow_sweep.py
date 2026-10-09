"""End-to-end GitHub collector sweep and removal reconciliation flows."""

import base64
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from tests.e2e.harness import E2EWorld, OutboundRecorder, admin_post, bearer, make_world
from tests.support.samples import Sample, aws_access_key_id


@dataclass(frozen=True)
class TreeEntry:
    """One GitHub tree response entry serialized at the HTTP boundary."""

    path: str
    kind: str
    sha: str
    size: int


@dataclass
class GitHubSweepMock:
    """Serve repository, tree and blob responses for sweep scenarios."""

    initial: bool = True
    fail_repo_b: bool = False
    secret_text: str = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Return deterministic GitHub API responses for the requested path."""
        path: str = request.url.path
        names: tuple[str, ...] = ("a", "b", "c")
        repo_name: str = next((name for name in names if f"/repos/acme/{name}" in path), "")
        if path.startswith("/api/v3/repos/acme/") and path.count("/") == 5:
            repo_id: int = names.index(repo_name) + 1
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": repo_id,
                    "name": repo_name,
                    "owner": {"login": "acme"},
                    "default_branch": "main",
                },
            )
        if path.startswith("/api/v3/repositories/"):
            repo_id = int(path.rsplit("/", 1)[-1])
            repo_name = names[repo_id - 1]
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": repo_id,
                    "name": repo_name,
                    "owner": {"login": "acme"},
                    "default_branch": "main",
                },
            )
        if "/branches/" in path:
            return httpx.Response(200, request=request, json={"commit": {"sha": "head"}})
        if "/git/trees/" in path:
            entries: list[TreeEntry] = []
            if self.initial and repo_name == "c":
                entries.append(
                    TreeEntry("old/secret.txt", "blob", "oldsha", len(self.secret_text.encode()))
                )
            elif not self.initial:
                entries.append(TreeEntry("safe.txt", "blob", f"safe-{repo_name}", 4))
            return httpx.Response(
                200,
                request=request,
                json={
                    "truncated": False,
                    "tree": [
                        {"path": item.path, "type": item.kind, "sha": item.sha, "size": item.size}
                        for item in entries
                    ],
                },
            )
        if "/git/blobs/" in path:
            sha: str = path.rsplit("/", 1)[-1]
            if self.fail_repo_b and sha == "safe-b":
                return httpx.Response(
                    500, request=request, json={"message": "private upstream detail"}
                )
            content: bytes = self.secret_text.encode() if sha == "oldsha" else b"safe"
            return httpx.Response(
                200,
                request=request,
                json={
                    "content": base64.b64encode(content).decode(),
                    "encoding": "base64",
                    "size": len(content),
                    "sha": sha,
                },
            )
        if "/contents/" in path:
            content = self.secret_text.encode()
            return httpx.Response(
                200,
                request=request,
                json={
                    "content": base64.b64encode(content).decode(),
                    "encoding": "base64",
                    "size": len(content),
                    "sha": "oldsha",
                },
            )
        return httpx.Response(404, request=request, json={})


async def _make_sweep_world(
    tmp_path: Path, fail_repo_b: bool = False
) -> tuple[E2EWorld, str, GitHubSweepMock]:
    """Create a three-repository collector and its initial issue state."""
    sample: Sample = aws_access_key_id()
    github: GitHubSweepMock = GitHubSweepMock(True, fail_repo_b, f"access_key={sample.value}")
    outbound: OutboundRecorder = OutboundRecorder(handlers=[("github.example.invalid", github)])
    world: E2EWorld = await make_world(
        tmp_path,
        (("E2E_GH_TOKEN", "e2e-github-" + "g" * 40),),
        outbound,
    )
    response: httpx.Response = await admin_post(
        world,
        "/api/collectors",
        json.dumps(
            {
                "name": "e2e-sweep",
                "kind": "github",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": json.dumps(
                    {"owner": "acme", "repos": ["a", "b", "c"], "branches": ["main"]}
                    | {"path_globs": []}
                ),
                "credential_ref": "env:E2E_GH_TOKEN",
                "enabled": True,
            }
        ),
    )
    assert response.status_code == 200, response.text
    config_id: str = response.json()["id"]
    response = await world.client.post(
        f"/api/collectors/{config_id}/run", headers=bearer(world.admin_token)
    )
    world.responses.append(response.text)
    assert response.status_code == 200, response.text
    await world.drain()
    initial_issues: httpx.Response = await world.client.get(
        "/api/issues?source=github", headers=bearer(world.admin_token)
    )
    world.responses.append(initial_issues.text)
    assert len(initial_issues.json()["items"]) == 1, "\n".join(
        (initial_issues.text, *(request.path for request in outbound.requests), *outbound.failures)
    )
    github.initial = False
    return world, config_id, github


async def _launch_sweep(world: E2EWorld, config_id: str) -> httpx.Response:
    """Launch a sweep through the public collectors endpoint."""
    response: httpx.Response = await world.client.post(
        f"/api/collectors/{config_id}/sweep", headers=bearer(world.admin_token)
    )
    world.responses.append(response.text)
    return response


@pytest.mark.asyncio
async def test_sweep_reconciles_removed_resource_after_success(tmp_path: Path) -> None:
    """A complete three-shard sweep resolves an issue for a removed resource."""
    setup: tuple[E2EWorld, str, GitHubSweepMock] = await _make_sweep_world(tmp_path)
    world: E2EWorld = setup[0]
    config_id: str = setup[1]
    try:
        issues: httpx.Response = await world.client.get(
            "/api/issues?source=github", headers=bearer(world.admin_token)
        )
        world.responses.append(issues.text)
        issue_id: str = issues.json()["items"][0]["id"]
        response: httpx.Response = await _launch_sweep(world, config_id)
        assert response.status_code == 200, response.text
        job_id: str = response.json()["map_job_id"]
        conflict: httpx.Response = await _launch_sweep(world, config_id)
        assert conflict.status_code == 409 and "conflict" in conflict.text
        await world.drain()
        job: httpx.Response = await world.client.get(
            f"/api/map-jobs/{job_id}", headers=bearer(world.admin_token)
        )
        world.responses.append(job.text)
        assert job.json()["status"] == "succeeded", "\n".join(
            (
                job.text,
                *(request.path for request in world.outbound.requests),
                *world.outbound.failures,
            )
        )
        assert job.json()["partitions_total"] == 1
        assert job.json()["partitions_done"] == 1
        assert job.json()["counts"]["succeeded"] == 3
        issue_after: httpx.Response = await world.client.get(
            f"/api/issues/{issue_id}", headers=bearer(world.admin_token)
        )
        world.responses.append(issue_after.text)
        assert issue_after.json()["status"] == "resolved", issue_after.text
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_partial_sweep_does_not_reconcile_removed_resources(tmp_path: Path) -> None:
    """A failed blob fetch makes the sweep partial and leaves the prior issue open."""
    setup: tuple[E2EWorld, str, GitHubSweepMock] = await _make_sweep_world(
        tmp_path, fail_repo_b=True
    )
    world: E2EWorld = setup[0]
    config_id: str = setup[1]
    try:
        issues: httpx.Response = await world.client.get(
            "/api/issues?source=github", headers=bearer(world.admin_token)
        )
        world.responses.append(issues.text)
        issue_id: str = issues.json()["items"][0]["id"]
        response: httpx.Response = await _launch_sweep(world, config_id)
        assert response.status_code == 200, response.text
        job_id: str = response.json()["map_job_id"]
        await world.drain()
        job: httpx.Response = await world.client.get(
            f"/api/map-jobs/{job_id}", headers=bearer(world.admin_token)
        )
        world.responses.append(job.text)
        assert job.json()["status"] == "partial", job.text
        issue_after: httpx.Response = await world.client.get(
            f"/api/issues/{issue_id}", headers=bearer(world.admin_token)
        )
        world.responses.append(issue_after.text)
        assert issue_after.json()["status"] == "unresolved", issue_after.text
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_concurrent_sweep_start_returns_conflict(tmp_path: Path) -> None:
    """A second start for an active collector sweep is rejected with HTTP 409."""
    setup: tuple[E2EWorld, str, GitHubSweepMock] = await _make_sweep_world(tmp_path)
    world: E2EWorld = setup[0]
    config_id: str = setup[1]
    try:
        first: httpx.Response = await _launch_sweep(world, config_id)
        second: httpx.Response = await _launch_sweep(world, config_id)
        assert first.status_code == 200
        assert second.status_code == 409 and "conflict" in second.text
        await world.drain()
    finally:
        await world.close()
