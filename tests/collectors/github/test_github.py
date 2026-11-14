"""GitHub collector behavior tests using an in-memory HTTP transport."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import httpx
import pytest

from telescreen.collectors.github import GitHubAdapter, GitHubApi, GitHubSweep
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_int,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import CollectorKind, ErrorCode, TriggerMode
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    EventCheck,
    EventCheckKind,
    EventResources,
    GitHubSelector,
    InboundWebhook,
    RateLimited,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
)
from telescreen.ports.sweeps import (
    PartitionPage,
    PartitionRequest,
    ShardEntry,
    ShardEntryKind,
    ShardSpec,
)
from tests.support.samples import Sample, aws_secret_access_key


def _config(
    repos: tuple[str, ...] = ("repo",),
    branches: tuple[str, ...] = (),
    path_globs: tuple[str, ...] = (),
) -> CollectorConfig:
    """Create one selected GitHub collector config."""
    return CollectorConfig(
        "config-1",
        "github",
        CollectorKind.GITHUB,
        TriggerMode.BOTH,
        "* * * * *",
        None,
        16,
        GitHubSelector("acme", repos, branches, path_globs),
        "env:GITHUB_TOKEN",
        True,
    )


def _api(handler: httpx.MockTransport) -> GitHubApi:
    """Build an API client backed by a fake transport."""
    return GitHubApi(
        httpx.AsyncClient(transport=handler), "https://api.github.test/", SecretValue("token")
    )


def _adapter(api: GitHubApi, host: str = "github.com", max_bytes: int = 1024) -> GitHubAdapter:
    """Build an adapter returning the same fake API client."""
    return GitHubAdapter(lambda config: api, host, max_bytes)


def _event(name: str, body: bytes, signature: str | None = None) -> InboundWebhook:
    """Create an inbound GitHub event envelope."""
    headers: list[tuple[str, str]] = [("X-GitHub-Event", name)]
    if signature is not None:
        headers.append(("X-Hub-Signature-256", signature))
    return InboundWebhook(CollectorKind.GITHUB, "POST", datetime.now(UTC), tuple(headers), body)


@pytest.mark.asyncio
async def test_webhook_signatures_and_push_resources() -> None:
    """Verify HMAC signatures and preserve added/modified/removed semantics."""
    payload: bytes = json.dumps(
        {
            "ref": "refs/heads/main",
            "forced": False,
            "repository": {
                "id": 1,
                "name": "repo",
                "default_branch": "main",
                "owner": {"login": "acme"},
            },
            "commits": [{"added": ["new.txt"], "modified": ["edit.txt"], "removed": ["gone.txt"]}],
        }
    ).encode()
    signature: str = "sha256=" + hmac.new(b"hook-secret", payload, hashlib.sha256).hexdigest()
    handler: httpx.MockTransport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    api: GitHubApi = _api(handler)
    adapter: GitHubAdapter = _adapter(api)
    secrets: CollectorSecrets = CollectorSecrets("hook-secret", None)
    valid: EventCheck = await adapter.verify_event(_event("push", payload, signature), secrets)
    invalid: EventCheck = await adapter.verify_event(_event("push", payload, "sha256=bad"), secrets)
    missing: EventCheck = await adapter.verify_event(_event("push", payload), secrets)
    ping: EventCheck = await adapter.verify_event(_event("ping", b"{}"), secrets)
    assert valid.kind is EventCheckKind.VALID
    assert invalid.kind is EventCheckKind.INVALID
    assert missing.kind is EventCheckKind.INVALID
    assert ping.kind is EventCheckKind.VALID
    resources: EventResources = await adapter.resources_from_event(
        _event("push", payload, signature), _config()
    )
    assert tuple(ref.raw_locator.rsplit(":", 1)[-1] for ref in resources.changed) == (
        "new.txt",
        "edit.txt",
    )
    assert tuple(ref.raw_locator.rsplit(":", 1)[-1] for ref in resources.removed) == ("gone.txt",)
    forced: bytes = payload.replace(b'"forced": false', b'"forced": true')
    forced_resources: EventResources = await adapter.resources_from_event(
        _event("push", forced), _config()
    )
    assert forced_resources.poll_changes
    unselected: EventResources = await adapter.resources_from_event(
        _event("push", payload.replace(b"refs/heads/main", b"refs/heads/other")),
        _config(branches=("main",)),
    )
    assert unselected.changed == ()
    await api.http.aclose()


@pytest.mark.asyncio
async def test_pull_request_refs_are_not_sweep_covered() -> None:
    """PR changed paths use PR refs and fetch as uncovered resources."""
    body: bytes = json.dumps(
        {
            "action": "opened",
            "number": 7,
            "repository": {"id": 1, "name": "repo", "owner": {"login": "acme"}},
        }
    ).encode()

    def respond(request: httpx.Request) -> httpx.Response:
        if "/pulls/7/files" in str(request.url):
            return httpx.Response(200, json=[{"filename": "secret.txt", "status": "modified"}])
        return httpx.Response(
            200,
            json={
                "content": base64.b64encode(b"safe\n").decode(),
                "encoding": "base64",
                "size": 5,
                "sha": "abcdef0123",
            },
        )

    api: GitHubApi = _api(httpx.MockTransport(respond))
    adapter: GitHubAdapter = _adapter(api)
    refs: EventResources = await adapter.resources_from_event(
        _event("pull_request", body), _config()
    )
    assert len(refs.changed) == 1
    assert "@refs/pull/7/head:" in refs.changed[0].raw_locator
    content: ResourceContent = await adapter.fetch(_config(), refs.changed[0])
    assert content.complete
    assert not content.sweep_covered
    assert content.segments[0].text == "safe"
    assert content.segments[0].locator == "L1@abcdef0"
    await api.http.aclose()


@pytest.mark.asyncio
async def test_fetch_text_binary_gone_and_rate_limit() -> None:
    """Decode source text, fail closed for binary content, and classify API errors."""
    sample: Sample = aws_secret_access_key()
    body: bytes = ("prefix\n" + sample.value + "\nsuffix").encode()

    def respond(request: httpx.Request) -> httpx.Response:
        url: str = str(request.url)
        if "/contents/" not in url:
            return httpx.Response(200, json={"default_branch": "main"})
        if url.endswith("/missing.txt?ref=refs%2Fheads%2Fmain"):
            return httpx.Response(404, json={"message": "not included in safe errors"})
        if url.endswith("/limited.txt?ref=refs%2Fheads%2Fmain"):
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
        if url.endswith("/binary.txt?ref=refs%2Fheads%2Fmain"):
            data: bytes = b"\xff\x00"
            return httpx.Response(
                200,
                json={
                    "content": base64.b64encode(data).decode(),
                    "encoding": "base64",
                    "size": len(data),
                    "sha": "abcdef0",
                },
            )
        return httpx.Response(
            200,
            json={
                "content": base64.b64encode(body).decode(),
                "encoding": "base64",
                "size": len(body),
                "sha": "abcdef0123",
            },
        )

    api: GitHubApi = _api(httpx.MockTransport(respond))
    adapter: GitHubAdapter = _adapter(api)
    ref: ResourceRef = adapter.ref_from_json(
        '{"host":"github.com","owner":"acme","repo":"repo",'
        '"ref":"refs/heads/main","path":"file.txt"}'
    )
    fetched: ResourceContent = await adapter.fetch(_config(), ref)
    assert fetched.complete
    assert fetched.sweep_covered
    assert tuple(segment.locator for segment in fetched.segments) == (
        "L1@abcdef0",
        "L2@abcdef0",
        "L3@abcdef0",
    )
    assert fetched.segments[1].text == sample.value
    other_branch: ResourceRef = adapter.ref_from_json(
        '{"host":"github.com","owner":"acme","repo":"repo",'
        '"ref":"refs/heads/other","path":"file.txt"}'
    )
    uncovered: ResourceContent = await adapter.fetch(_config(branches=("main",)), other_branch)
    assert not uncovered.sweep_covered
    binary: ResourceRef = adapter.ref_from_json(
        '{"host":"github.com","owner":"acme","repo":"repo",'
        '"ref":"refs/heads/main","path":"binary.txt"}'
    )
    assert not (await adapter.fetch(_config(), binary)).complete
    missing: ResourceRef = adapter.ref_from_json(
        '{"host":"github.com","owner":"acme","repo":"repo",'
        '"ref":"refs/heads/main","path":"missing.txt"}'
    )
    with pytest.raises(ResourceGoneError):
        await adapter.fetch(_config(), missing)
    limited: ResourceRef = adapter.ref_from_json(
        '{"host":"github.com","owner":"acme","repo":"repo",'
        '"ref":"refs/heads/main","path":"limited.txt"}'
    )
    with pytest.raises(RateLimited):
        await adapter.fetch(_config(), limited)
    await api.http.aclose()


@pytest.mark.asyncio
async def test_fetch_uses_blob_for_contents_without_base64_encoding() -> None:
    """Decode large text through git blobs and fail closed when it is unavailable."""
    sample: Sample = aws_secret_access_key()
    body: bytes = ("prefix\n" + sample.value + "\nsuffix").encode()
    blob_requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        url: str = str(request.url)
        if "/contents/large.txt" in url:
            return httpx.Response(
                200,
                json={"content": "", "encoding": "none", "size": 2_000_000, "sha": "abcdef0123"},
            )
        if "/contents/unavailable.txt" in url:
            return httpx.Response(
                200,
                json={"content": "", "encoding": "none", "size": 2_000_000, "sha": "unavailable"},
            )
        if "/git/blobs/" in url:
            blob_requests.append(url.rsplit("/", 1)[-1])
            if url.endswith("/git/blobs/abcdef0123"):
                return httpx.Response(
                    200,
                    json={
                        "content": base64.b64encode(body).decode(),
                        "encoding": "base64",
                        "size": len(body),
                        "sha": "abcdef0123",
                    },
                )
            return httpx.Response(200, json={"content": "", "encoding": "base64"})
        if "/repos/acme/repo" in url:
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    adapter: GitHubAdapter = _adapter(api, max_bytes=5 * 1024 * 1024)
    large: ResourceRef = adapter.ref_from_json(
        '{"host":"github.com","owner":"acme","repo":"repo",'
        '"ref":"refs/heads/main","path":"large.txt"}'
    )
    fetched: ResourceContent = await adapter.fetch(_config(), large)
    assert fetched.complete and len(fetched.segments) == 3
    assert fetched.segments[1].text == sample.value
    assert fetched.segments[1].locator == "L2@abcdef0"
    unavailable: ResourceRef = adapter.ref_from_json(
        '{"host":"github.com","owner":"acme","repo":"repo",'
        '"ref":"refs/heads/main","path":"unavailable.txt"}'
    )
    rejected: ResourceContent = await adapter.fetch(_config(), unavailable)
    assert not rejected.complete and rejected.segments == ()
    assert blob_requests == ["abcdef0123", "unavailable"]
    await api.http.aclose()


@pytest.mark.asyncio
async def test_first_schedule_run_pages_full_tree_listing() -> None:
    """A missing cursor requests a full listing over every selected target."""
    paths: list[JsonValue] = [
        json_object(
            (
                ("path", json_str(f"file-{index}.txt")),
                ("type", json_str("blob")),
                ("sha", json_str("abc")),
            )
        )
        for index in range(101)
    ]

    compare_requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        url: str = str(request.url)
        if "/repos/acme/" in url and "/branches/" not in url and "/git/trees/" not in url:
            name: str = url.split("/repos/acme/")[1].split("?")[0]
            return httpx.Response(
                200, json={"id": 1 if name == "repo" else 2, "name": name, "default_branch": "main"}
            )
        if "/branches/main" in url:
            return httpx.Response(200, json={"commit": {"sha": "head"}})
        if "/compare/" in url:
            compare_requests.append(url)
            return httpx.Response(200, json={"status": "identical", "files": []})
        if "/git/trees/" in url:
            return httpx.Response(
                200,
                content=dump_json(
                    json_object((("truncated", parse_json("false")), ("tree", json_array(paths))))
                ),
            )
        return httpx.Response(404, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    adapter: GitHubAdapter = _adapter(api)
    page1: ChangePage = await adapter.list_changed(_config(repos=("repo", "repo2")), None)
    assert page1.full_listing and page1.has_more
    assert len(page1.changed) == 100
    page2: ChangePage = await adapter.list_changed(
        _config(repos=("repo", "repo2")), page1.next_cursor
    )
    assert page2.full_listing and page2.has_more
    assert len(page2.changed) == 100
    page3: ChangePage = await adapter.list_changed(
        _config(repos=("repo", "repo2")), page2.next_cursor
    )
    assert page3.full_listing and not page3.has_more
    assert len(page3.changed) == 2
    assert compare_requests == []
    await api.http.aclose()


@pytest.mark.asyncio
async def test_steady_state_and_identical_compare_are_incremental_noops() -> None:
    """Unchanged heads skip compare and an identical compare yields no changes."""
    compare_requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        url: str = str(request.url)
        if "/branches/main" in url:
            return httpx.Response(200, json={"commit": {"sha": "head"}})
        if "/compare/" in url:
            compare_requests.append(url)
            return httpx.Response(200, json={"status": "identical", "files": []})
        if "/repos/acme/repo" in url:
            return httpx.Response(200, json={"id": 1, "name": "repo", "default_branch": "main"})
        return httpx.Response(404, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    adapter: GitHubAdapter = _adapter(api)
    cursor: CollectorCursor = CollectorCursor('{"acme/repo@main":"head"}')
    page: ChangePage = await adapter.list_changed(_config(), cursor)
    assert not page.full_listing and not page.has_more
    assert page.changed == () and page.removed == ()
    steady_cursor: CollectorCursor | None = page.next_cursor
    assert steady_cursor is not None
    assert steady_cursor.value == cursor.value
    assert compare_requests == []
    older_cursor: CollectorCursor = CollectorCursor('{"acme/repo@main":"old"}')
    identical: ChangePage = await adapter.list_changed(_config(), older_cursor)
    assert not identical.full_listing and not identical.has_more
    assert identical.changed == () and identical.removed == ()
    assert len(compare_requests) == 1
    await api.http.aclose()


@pytest.mark.asyncio
async def test_full_listing_pins_head_when_branch_moves_between_pages() -> None:
    """All continuation pages use the initial head and leave it in the cursor."""
    paths: list[JsonValue] = [
        json_object(
            (
                ("path", json_str(f"file-{index}.txt")),
                ("type", json_str("blob")),
                ("sha", json_str("blob")),
            )
        )
        for index in range(205)
    ]
    branch_requests: int = 0
    tree_shas: list[str] = []
    comparisons: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal branch_requests
        url: str = str(request.url)
        if "/branches/main" in url:
            branch_requests += 1
            head: str = "h1" if branch_requests == 1 else "h2"
            return httpx.Response(200, json={"commit": {"sha": head}})
        if "/git/trees/" in url:
            tree_shas.append(url.rsplit("/", 1)[-1].split("?", 1)[0])
            return httpx.Response(
                200,
                content=dump_json(
                    json_object((("truncated", parse_json("false")), ("tree", json_array(paths))))
                ),
            )
        if "/compare/" in url:
            comparisons.append(url)
            return httpx.Response(
                200,
                json={"status": "ahead", "files": [{"filename": "late.txt", "status": "added"}]},
            )
        if "/repos/acme/repo" in url:
            return httpx.Response(200, json={"id": 1, "name": "repo", "default_branch": "main"})
        return httpx.Response(404, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    adapter: GitHubAdapter = _adapter(api)
    config: CollectorConfig = _config()
    first: ChangePage = await adapter.list_changed(config, None)
    first_cursor: CollectorCursor | None = first.next_cursor
    assert first_cursor is not None
    second: ChangePage = await adapter.list_changed(config, first_cursor)
    second_cursor: CollectorCursor | None = second.next_cursor
    assert second_cursor is not None
    third: ChangePage = await adapter.list_changed(config, second_cursor)
    assert len(first.changed) == 100 and first.has_more
    assert len(second.changed) == 100 and second.has_more
    assert len(third.changed) == 5 and not third.has_more
    assert tree_shas == ["h1", "h1", "h1"]
    third_cursor: CollectorCursor | None = third.next_cursor
    assert third_cursor is not None
    assert '"acme/repo@main":"h1"' in third_cursor.value
    assert "__offset" not in third_cursor.value
    assert comparisons == []
    following: ChangePage = await adapter.list_changed(config, third_cursor)
    assert not following.full_listing
    assert len(following.changed) == 1
    assert "/compare/h1...h2" in comparisons[0]
    await api.http.aclose()


@pytest.mark.asyncio
async def test_incremental_rename_and_rewrite_fallback() -> None:
    """Compare renames emit remove plus add, and compare 404 triggers a full tree."""
    mode: str = "ahead"

    def respond(request: httpx.Request) -> httpx.Response:
        url: str = str(request.url)
        if (
            "/repos/acme/repo" in url
            and "/branches/" not in url
            and "/compare/" not in url
            and "/git/trees/" not in url
        ):
            return httpx.Response(200, json={"id": 1, "name": "repo", "default_branch": "main"})
        if "/branches/main" in url:
            return httpx.Response(200, json={"commit": {"sha": "new"}})
        if "/compare/" in url:
            if mode == "rewrite":
                return httpx.Response(404, json={})
            return httpx.Response(
                200,
                json={
                    "status": "ahead",
                    "files": [
                        {"filename": "new.txt", "previous_filename": "old.txt", "status": "renamed"}
                    ],
                },
            )
        if "/git/trees/" in url:
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [{"path": "new.txt", "type": "blob", "sha": "abc"}],
                },
            )
        return httpx.Response(404, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    adapter: GitHubAdapter = _adapter(api)
    cursor: CollectorCursor = CollectorCursor('{"acme/repo@main":"old"}')
    incremental: ChangePage = await adapter.list_changed(_config(), cursor)
    assert [ref.raw_locator.rsplit(":", 1)[-1] for ref in incremental.removed] == ["old.txt"]
    assert [ref.raw_locator.rsplit(":", 1)[-1] for ref in incremental.changed] == ["new.txt"]
    filtered: ChangePage = await adapter.list_changed(_config(path_globs=("old.txt",)), cursor)
    assert [ref.raw_locator.rsplit(":", 1)[-1] for ref in filtered.removed] == ["old.txt"]
    assert filtered.changed == ()
    mode = "rewrite"
    rewritten: ChangePage = await adapter.list_changed(_config(), cursor)
    assert rewritten.full_listing and len(rewritten.changed) == 1
    await api.http.aclose()


@pytest.mark.asyncio
async def test_sweep_partitions_branches_and_streams_entries() -> None:
    """Create six stable shards and stream a fetched file from a branch tree."""
    secret: str = aws_secret_access_key().value
    encoded: str = base64.b64encode(secret.encode()).decode()

    def respond(request: httpx.Request) -> httpx.Response:
        url: str = str(request.url)
        if (
            "/repos/acme/" in url
            and "/repositories/" not in url
            and "/branches/" not in url
            and "/git/" not in url
        ):
            name: str = url.split("/repos/acme/")[1].split("?")[0]
            return httpx.Response(
                200, json={"id": int(name[-1]), "name": name, "default_branch": "main"}
            )
        if "/repositories/" in url:
            return httpx.Response(200, json={"id": 1, "name": "repo1", "owner": {"login": "acme"}})
        if "/branches/main" in url:
            return httpx.Response(200, json={"commit": {"sha": "head"}})
        if "/git/trees/" in url:
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {"path": "secret.txt", "type": "blob", "sha": "abc", "size": len(secret)},
                        {"path": "large.dat", "type": "blob", "sha": "large", "size": 2048},
                    ],
                },
            )
        if "/git/blobs/" in url:
            return httpx.Response(200, json={"content": encoded, "encoding": "base64"})
        return httpx.Response(404, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    config: CollectorConfig = _config(repos=("repo1", "repo2", "repo3"), branches=("main", "dev"))
    sweep: GitHubSweep = GitHubSweep(lambda selected: api, "github.com", 1024)
    page: PartitionPage = await sweep.partition(config, PartitionRequest("", None, config.selector))
    assert len(page.shards) == 6
    assert len({shard.shard_key for shard in page.shards}) == 6
    entries: list[ShardEntry] = [
        entry async for entry in sweep.shard_resources(config, page.shards[0])
    ]
    assert len(entries) == 2 and all(entry.kind is ShardEntryKind.FETCHED for entry in entries)
    assert entries[0].content is not None and entries[0].content.sweep_covered
    assert entries[0].content.segments[0].text == secret
    assert entries[1].content is not None
    assert not entries[1].content.complete and entries[1].content.segments == ()
    await api.http.aclose()


@pytest.mark.asyncio
async def test_sweep_truncated_tree_fails_closed() -> None:
    """A truncated recursive tree fails the whole repository shard."""

    def respond(request: httpx.Request) -> httpx.Response:
        url: str = str(request.url)
        if "/repositories/" in url:
            return httpx.Response(200, json={"id": 1, "name": "repo", "owner": {"login": "acme"}})
        if "/branches/main" in url:
            return httpx.Response(200, json={"commit": {"sha": "head"}})
        if "/git/trees/" in url:
            return httpx.Response(200, json={"truncated": True, "tree": []})
        return httpx.Response(404, json={})

    api: GitHubApi = _api(httpx.MockTransport(respond))
    config: CollectorConfig = _config()
    sweep: GitHubSweep = GitHubSweep(lambda selected: api, "github.com", 1024)
    descriptor: JsonValue = json_object((("repo_id", json_int(1)), ("branch", json_str("main"))))
    shard: ShardSpec = ShardSpec("key", descriptor, 1)
    with pytest.raises(TelescreenError) as caught:
        _ = [entry async for entry in sweep.shard_resources(config, shard)]
    assert caught.value.code is ErrorCode.FETCH_FAILED
    await api.http.aclose()
