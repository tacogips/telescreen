"""GitHub v1.1 exposure, placement, ownership, and coverage tests."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import httpx
import pytest

from telescreen.collectors.github import GitHubAdapter, GitHubApi, GitHubSweep
from telescreen.collectors.github.codeowners import match_owner
from telescreen.collectors.github.exposure import repo_exposure
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_int, json_object, json_str, parse_json
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import (
    CollectorKind,
    CoverageState,
    ErrorCode,
    IdentityKind,
    PlacementKind,
    RepoVisibility,
    TriggerMode,
    Visibility,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import (
    CollectorConfig,
    EventResources,
    Exposure,
    GitHubSelector,
    InboundWebhook,
    OwnerIdentity,
    RateLimited,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
)
from telescreen.ports.sweeps import ShardEntry, ShardEntryKind, ShardSpec


def _config() -> CollectorConfig:
    """Create one selected GitHub collector configuration."""
    return CollectorConfig(
        "config-1",
        "github",
        CollectorKind.GITHUB,
        TriggerMode.BOTH,
        "* * * * *",
        None,
        16,
        GitHubSelector("Acme", ("Repo",), (), ()),
        "env:GITHUB_TOKEN",
        True,
    )


def _api(handler: httpx.MockTransport) -> GitHubApi:
    """Build an API client backed by a fake transport."""
    return GitHubApi(
        httpx.AsyncClient(transport=handler),
        "https://api.github.test/",
        SecretValue("token"),
    )


def _ref() -> ResourceRef:
    """Create a resource reference without embedding provider metadata."""
    return ResourceRef(
        CollectorKind.GITHUB,
        '{"host":"github.com","owner":"Acme","repo":"Repo",'
        '"ref":"refs/heads/main","path":"secrets/a.txt"}',
        "github:github.com/Acme/Repo@refs/heads/main:secrets/a.txt",
    )


def test_codeowners_patterns_and_owner_selection() -> None:
    """Apply last-match wins, root and recursive patterns, and supported owners."""
    text: str = "* @default\n/secrets/ @org/team @sec-owner\n**/*.md a@corp.test\n"
    secret: OwnerIdentity | None = match_owner(text, "secrets/a.txt")
    document: OwnerIdentity | None = match_owner(text, "docs/a.md")
    nested: OwnerIdentity | None = match_owner("secret @bare\n", "deep/secret")
    assert secret is not None
    assert secret.kind is IdentityKind.GITHUB_LOGIN
    assert secret.value == "sec-owner"
    assert document is not None and document.kind is IdentityKind.EMAIL
    assert document.value == "a@corp.test"
    assert nested is not None and nested.value == "bare"
    assert match_owner("/root.txt @root\n", "deep/root.txt") is None


def test_repository_exposure_visibility_and_private_fallback() -> None:
    """Map repository visibility and fork metadata to closed exposure values."""
    public: Exposure = repo_exposure(parse_json('{"visibility":"public","fork":false}'))
    internal: Exposure = repo_exposure(parse_json('{"visibility":"internal"}'))
    fallback_public: Exposure = repo_exposure(parse_json('{"private":false}'))
    private_fork: Exposure = repo_exposure(parse_json('{"private":true,"fork":true}'))
    assert public.visibility is Visibility.PUBLIC
    assert public.repo_visibility is RepoVisibility.PUBLIC
    assert fallback_public.visibility is Visibility.PUBLIC
    assert fallback_public.repo_visibility is RepoVisibility.PUBLIC
    assert internal.visibility is Visibility.DOMAIN
    assert internal.repo_visibility is RepoVisibility.INTERNAL
    assert private_fork.visibility is Visibility.PRIVATE
    assert private_fork.repo_visibility is RepoVisibility.PRIVATE
    assert private_fork.is_fork


@pytest.mark.asyncio
async def test_adapter_populates_context_and_maps_non_rate_limit_403() -> None:
    """Populate exposure, CODEOWNERS owner, placement, and coverage transiently."""
    methods: list[str] = []
    codeowners_requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        path: str = request.url.path
        if path.endswith("/repos/Acme/Repo"):
            return httpx.Response(
                200,
                json={
                    "visibility": "public",
                    "private": False,
                    "fork": True,
                    "default_branch": "main",
                },
            )
        if "/contents/.github/CODEOWNERS" in str(request.url):
            codeowners_requests.append(path)
            return httpx.Response(404, json={})
        if "/contents/CODEOWNERS" in str(request.url):
            codeowners_requests.append(path)
            value: str = base64.b64encode(b"/secrets/ @sec-owner\n").decode()
            return httpx.Response(200, json={"content": value, "encoding": "base64"})
        if "/contents/secrets/a.txt" in str(request.url):
            return httpx.Response(403, json={})
        return httpx.Response(404, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    adapter: GitHubAdapter = GitHubAdapter(lambda config: api, "github.com", 128)
    config: CollectorConfig = _config()
    event: InboundWebhook = InboundWebhook(
        CollectorKind.GITHUB,
        "POST",
        datetime(2026, 10, 10, tzinfo=UTC),
        (("X-GitHub-Event", "push"),),
        b'{"repository":{"owner":{"login":"Acme"},"name":"Repo",'
        b'"default_branch":"main"},"ref":"refs/heads/main","forced":false,'
        b'"commits":[{"modified":["secrets/a.txt"]}]}',
    )
    resources: EventResources = await adapter.resources_from_event(event, config)
    assert len(resources.changed) == 1
    content: ResourceContent = await adapter.fetch(config, resources.changed[0])
    assert content.coverage is not None
    assert content.coverage.state is CoverageState.PERMISSION_DENIED
    assert content.exposure is not None and content.exposure.visibility is Visibility.PUBLIC
    assert content.exposure.is_fork
    assert content.owner is not None and content.owner.value == "sec-owner"
    assert content.owner.kind is IdentityKind.GITHUB_LOGIN
    assert content.placement[0].kind is PlacementKind.GITHUB_OWNER
    assert content.placement[0].value == "acme"
    assert content.placement[1].value == "acme/repo"
    assert content.modified_at is None
    assert len(codeowners_requests) == 2
    assert methods and all(method == "GET" for method in methods)
    await api.http.aclose()


@pytest.mark.asyncio
async def test_push_uses_head_commit_for_owner_and_modified_time_without_storing_login() -> None:
    """Use the push head commit while keeping its author out of durable refs."""
    methods: list[str] = []
    head_sha: str = "59b2a8240d4663e784d36676fe995f0415498190"

    def respond(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        path: str = request.url.path
        if path.endswith("/repos/Acme/Repo"):
            return httpx.Response(200, json={"visibility": "private", "private": True})
        if path.endswith(f"/commits/{head_sha}"):
            return httpx.Response(
                200,
                json={
                    "author": {"login": "push-author"},
                    "commit": {
                        "author": {"date": "2026-10-10T09:30:00Z"},
                        "committer": {"date": "2026-10-10T09:31:00Z"},
                    },
                },
            )
        if path.endswith("/contents/secrets/a.txt"):
            encoded: str = base64.b64encode(b"safe\n").decode()
            return httpx.Response(
                200,
                json={"content": encoded, "encoding": "base64", "size": 5, "sha": "abc"},
            )
        if "/contents/" in path:
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    adapter: GitHubAdapter = GitHubAdapter(lambda config: api, "github.com", 128)
    event: InboundWebhook = InboundWebhook(
        CollectorKind.GITHUB,
        "POST",
        datetime(2026, 10, 10, tzinfo=UTC),
        (("X-GitHub-Event", "push"),),
        (
            f'{{"repository":{{"owner":{{"login":"Acme"}},"name":"Repo",'
            f'"default_branch":"main"}},"ref":"refs/heads/main","forced":false,'
            f'"head_commit":{{"id":"{head_sha}"}},"commits":[{{"modified":["secrets/a.txt"]}}]}}'
        ).encode(),
    )
    resources: EventResources = await adapter.resources_from_event(event, _config())
    resource_ref: ResourceRef = resources.changed[0]
    assert '"head_commit"' in resource_ref.ref_json
    assert head_sha not in resource_ref.ref_json
    assert Redactor.default().has_hits(resource_ref.ref_json) is False
    assert "push-author" not in resource_ref.ref_json
    assert "push-author" not in resource_ref.raw_locator
    restored_ref: ResourceRef = adapter.ref_from_json(resource_ref.ref_json)
    assert restored_ref.ref_json == resource_ref.ref_json
    assert restored_ref.raw_locator == "github:github.com/Acme/Repo@refs/heads/main:secrets/a.txt"
    content: ResourceContent = await adapter.fetch(_config(), restored_ref)
    assert content.owner is not None
    assert content.owner.kind is IdentityKind.GITHUB_LOGIN
    assert content.owner.value == "push-author"
    assert "push-author" not in repr(content.owner)
    assert content.modified_at is not None
    assert content.modified_at.isoformat() == "2026-10-10T09:30:00+00:00"
    assert len([method for method in methods if method == "GET"]) == len(methods)
    assert len(methods) == 6
    await api.http.aclose()


@pytest.mark.asyncio
async def test_sweep_uses_latest_path_commit_for_owner_and_modified_time() -> None:
    """Ignore malformed event SHA and use path history as a safe fallback."""
    commit_requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        path: str = request.url.path
        if path.endswith("/commits"):
            commit_requests.append(path)
            return httpx.Response(
                200,
                json=[
                    {
                        "author": {"login": "sweep-author"},
                        "commit": {"author": {"date": "2026-10-09T08:00:00Z"}},
                    }
                ],
            )
        if path.endswith("/repos/Acme/Repo"):
            return httpx.Response(200, json={"visibility": "private", "private": True})
        if path.endswith("/contents/secrets/a.txt"):
            encoded: str = base64.b64encode(b"safe\n").decode()
            return httpx.Response(
                200,
                json={"content": encoded, "encoding": "base64", "size": 5, "sha": "abc"},
            )
        return httpx.Response(404, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    adapter: GitHubAdapter = GitHubAdapter(lambda config: api, "github.com", 128)
    event: InboundWebhook = InboundWebhook(
        CollectorKind.GITHUB,
        "POST",
        datetime(2026, 10, 10, tzinfo=UTC),
        (("X-GitHub-Event", "push"),),
        b'{"repository":{"owner":{"login":"Acme"},"name":"Repo",'
        b'"default_branch":"main"},"ref":"refs/heads/main","forced":false,'
        b'"head_commit":{"id":"not-a-hex-sha"},"commits":[{"modified":["secrets/a.txt"]}]}',
    )
    resources: EventResources = await adapter.resources_from_event(event, _config())
    ref: ResourceRef = resources.changed[0]
    assert '"head_commit"' not in ref.ref_json
    assert '"head_sha"' not in ref.ref_json
    assert Redactor.default().has_hits(ref.ref_json) is False
    content: ResourceContent = await adapter.fetch(_config(), ref)
    assert "sweep-author" not in ref.ref_json
    assert content.owner is not None
    assert content.owner.value == "sweep-author"
    assert content.modified_at is not None
    assert content.modified_at.isoformat() == "2026-10-09T08:00:00+00:00"
    assert len(commit_requests) == 1
    await api.http.aclose()


@pytest.mark.asyncio
async def test_contents_and_blob_forbidden_preserve_quota_and_404() -> None:
    """Map non-quota 403 to FORBIDDEN while preserving quota and gone errors."""
    current: str = "forbidden"

    def respond(request: httpx.Request) -> httpx.Response:
        if current == "quota":
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
        if current == "gone":
            return httpx.Response(404, json={})
        return httpx.Response(403, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    with pytest.raises(TelescreenError) as contents_error:
        await api.contents("acme", "repo", "file.txt", "main")
    assert contents_error.value.code is ErrorCode.FORBIDDEN
    with pytest.raises(TelescreenError) as blob_error:
        await api.blob("acme", "repo", "sha")
    assert blob_error.value.code is ErrorCode.FORBIDDEN
    current = "quota"
    with pytest.raises(RateLimited):
        await api.contents("acme", "repo", "file.txt", "main")
    with pytest.raises(RateLimited):
        await api.blob("acme", "repo", "sha")
    current = "gone"
    with pytest.raises(ResourceGoneError):
        await api.contents("acme", "repo", "file.txt", "main")
    with pytest.raises(ResourceGoneError):
        await api.blob("acme", "repo", "sha")
    await api.http.aclose()


@pytest.mark.asyncio
async def test_sweep_keeps_forbidden_error_code_and_coverage_context() -> None:
    """Sweep errors retain FORBIDDEN and oversized blobs carry safe coverage."""

    def respond(request: httpx.Request) -> httpx.Response:
        path: str = request.url.path
        if path == "/repositories/7":
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "name": "Repo",
                    "owner": {"login": "Acme"},
                    "visibility": "internal",
                    "private": False,
                    "fork": False,
                },
            )
        if "/contents/" in path:
            return httpx.Response(404, json={})
        if path.endswith("/branches/main"):
            return httpx.Response(200, json={"commit": {"sha": "head"}})
        if "/git/trees/" in path:
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {"path": "blocked.txt", "type": "blob", "sha": "blocked", "size": 1},
                        {"path": "large.txt", "type": "blob", "sha": "large", "size": 129},
                    ],
                },
            )
        if path.endswith("/git/blobs/blocked"):
            return httpx.Response(403, json={})
        return httpx.Response(200, json={"content": "", "encoding": "base64"})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    config: CollectorConfig = _config()
    sweep: GitHubSweep = GitHubSweep(lambda selected: api, "github.com", 128)
    descriptor: JsonValue = json_object(
        (
            ("repo_id", json_int(7)),
            ("branch", json_str("main")),
        )
    )
    shard: ShardSpec = ShardSpec("key", descriptor, 1)
    entries: list[ShardEntry] = [entry async for entry in sweep.shard_resources(config, shard)]
    assert entries[0].kind is ShardEntryKind.FAILED
    assert entries[0].error_code is ErrorCode.FORBIDDEN
    assert entries[1].content is not None
    assert entries[1].content.coverage is not None
    assert entries[1].content.coverage.state is CoverageState.TOO_LARGE
    assert entries[1].content.exposure is not None
    assert entries[1].content.exposure.visibility is Visibility.DOMAIN
    assert entries[1].content.placement[0].value == "acme"
    await api.http.aclose()
