"""Tests for Slack exposure, owner, placement, modified time, and coverage context."""

from datetime import UTC, datetime

import httpx
import pytest

from telescreen.collectors.slack.adapter import SlackAdapter
from telescreen.collectors.slack.client import SlackApi, SlackConversationInfo, SlackFile
from telescreen.collectors.slack.exposure import slack_exposure
from telescreen.collectors.slack.sweep import SlackSweep
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import (
    ChannelKind,
    CollectorKind,
    CoverageState,
    IdentityKind,
    PlacementKind,
    TriggerMode,
    UnsupportedReason,
    Visibility,
)
from telescreen.ports.collectors import (
    CollectorConfig,
    Exposure,
    ResourceContent,
    ResourceRef,
    SlackSelector,
)
from telescreen.ports.sweeps import PartitionPage, PartitionRequest, ShardEntry, ShardEntryKind


def collector_config() -> CollectorConfig:
    """Return a Slack collector configuration for v1.1 context tests."""
    return CollectorConfig(
        id="collector-test",
        name="slack-test",
        kind=CollectorKind.SLACK,
        trigger_mode=TriggerMode.BOTH,
        cron="*/5 * * * *",
        sweep_cron=None,
        sweep_max_concurrency=1,
        selector=SlackSelector(("C1",), True),
        credential_ref="env:SLACK_BOT_TOKEN",
        enabled=True,
    )


def slack_api(http: httpx.AsyncClient) -> SlackApi:
    """Build a Slack API client for a mocked transport."""
    return SlackApi(http, SecretValue("test-token"))


@pytest.mark.parametrize(
    ("info", "visibility", "kind", "count"),
    [
        (SlackConversationInfo("C1"), Visibility.DOMAIN, ChannelKind.PUBLIC, 0),
        (
            SlackConversationInfo("C1", is_private=True),
            Visibility.INTERNAL,
            ChannelKind.PRIVATE,
            0,
        ),
        (SlackConversationInfo("C1", is_im=True), Visibility.PRIVATE, ChannelKind.DM, 0),
        (SlackConversationInfo("C1", is_mpim=True), Visibility.INTERNAL, ChannelKind.DM, 0),
        (
            SlackConversationInfo(
                "C1", is_ext_shared=True, connected_team_ids=("T1", "T2", "T3", "T3")
            ),
            Visibility.EXTERNAL_SHARED,
            ChannelKind.CONNECT,
            2,
        ),
    ],
)
def test_slack_exposure_maps_channel_kinds_and_external_count(
    info: SlackConversationInfo,
    visibility: Visibility,
    kind: ChannelKind,
    count: int,
) -> None:
    """Map Slack conversation details to closed exposure enums and counts."""
    exposure: Exposure | None = slack_exposure(info, "T1")
    assert exposure is not None
    assert exposure.visibility is visibility
    assert exposure.channel_kind is kind
    assert exposure.external_principal_count == count


def test_public_file_link_wins_over_channel_exposure() -> None:
    """Treat a shared public URL as wider than the containing channel."""
    info: SlackConversationInfo = SlackConversationInfo("C1", is_private=True)
    file: SlackFile = SlackFile(
        "F1", "ignored.txt", "text/plain", 1, "https://files.slack.com/F1", public_url_shared=True
    )
    exposure: Exposure | None = slack_exposure(info, "T1", file)
    assert exposure is not None
    assert exposure.visibility is Visibility.ANYONE_WITH_LINK
    assert exposure.channel_kind is ChannelKind.PRIVATE


async def test_message_fetch_attaches_context_and_uses_only_read_calls() -> None:
    """Attach Slack message context, hide owner ids, and use GET endpoints only."""
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method: str = request.url.path.rsplit("/", maxsplit=1)[-1]
        requests.append((request.method, method))
        if method == "conversations.history":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [
                        {
                            "ts": "1700000000.000100",
                            "text": "safe content",
                            "user": "U_PRIVATE",
                            "edited": {"ts": "1700000100.000200"},
                        }
                    ],
                },
            )
        if method == "conversations.info":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "channel": {
                        "id": "C1",
                        "is_ext_shared": True,
                        "connected_team_ids": ["T1", "T2", "T3"],
                    },
                },
            )
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = SlackAdapter(lambda config: slack_api(http), 1024)
        ref: ResourceRef = adapter._message_ref("T1", "C1", "1700000000.000100")
        content: ResourceContent = await adapter.fetch(collector_config(), ref)

    assert content.exposure is not None
    assert content.exposure.visibility is Visibility.EXTERNAL_SHARED
    assert content.exposure.channel_kind is ChannelKind.CONNECT
    assert content.exposure.external_principal_count == 2
    assert content.owner is not None
    assert content.owner.kind is IdentityKind.SLACK_USER
    assert content.owner.value == "U_PRIVATE"
    assert "U_PRIVATE" not in repr(content)
    assert content.placement[0].kind is PlacementKind.SLACK_CHANNEL
    assert content.placement[0].value == "C1"
    assert content.modified_at == datetime.fromtimestamp(1700000100.0002, UTC)
    assert content.coverage is not None
    assert content.coverage.state is CoverageState.SCANNED
    assert requests == [
        ("GET", "conversations.history"),
        ("GET", "conversations.info"),
    ]


@pytest.mark.parametrize(
    ("mimetype", "size", "state", "reason"),
    [
        ("image/png", 10, CoverageState.UNSUPPORTED, UnsupportedReason.IMAGE),
        (
            "application/octet-stream",
            10,
            CoverageState.UNSUPPORTED,
            UnsupportedReason.BINARY,
        ),
        ("text/plain", 2048, CoverageState.TOO_LARGE, None),
        ("application/pdf", 10, CoverageState.UNSUPPORTED, UnsupportedReason.UNSUPPORTED_TYPE),
    ],
)
async def test_file_fetch_reports_unsupported_and_too_large_coverage(
    mimetype: str,
    size: int,
    state: CoverageState,
    reason: UnsupportedReason | None,
) -> None:
    """Report files that cannot be scanned without pretending they were clean."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("files.info"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "file": {
                        "id": "F1",
                        "name": "ignored.bin",
                        "mimetype": mimetype,
                        "size": size,
                        "url_private": "https://files.slack.com/F1",
                        "timestamp": 1700000000,
                        "updated": 1700000200,
                        "user": "U_PRIVATE",
                        "channels": ["C1"],
                    },
                },
            )
        if request.url.path.endswith("conversations.info"):
            return httpx.Response(
                200,
                json={"ok": True, "channel": {"id": "C1", "is_private": True}},
            )
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = SlackAdapter(lambda config: slack_api(http), 1024)
        ref: ResourceRef = adapter._file_ref("T1", "F1")
        content: ResourceContent = await adapter.fetch(collector_config(), ref)

    assert content.coverage is not None
    assert content.coverage.state is state
    assert content.coverage.reason is reason
    assert content.owner is not None and content.owner.value == "U_PRIVATE"
    assert "U_PRIVATE" not in repr(content)
    assert content.placement[0].value == "C1"
    assert content.modified_at == datetime.fromtimestamp(1700000200, UTC)


async def test_conversation_info_failure_keeps_message_scan_available() -> None:
    """Treat unavailable exposure metadata as unknown while scanning the message."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("conversations.history"):
            return httpx.Response(
                200,
                json={"ok": True, "messages": [{"ts": "1700000000.0", "text": "safe"}]},
            )
        if request.url.path.endswith("conversations.info"):
            return httpx.Response(500)
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = SlackAdapter(lambda config: slack_api(http), 1024)
        ref: ResourceRef = adapter._message_ref("T1", "C1", "1700000000.0")
        content: ResourceContent = await adapter.fetch(collector_config(), ref)

    assert content.segments[0].text == "safe"
    assert content.exposure is None
    assert content.coverage is not None
    assert content.coverage.state is CoverageState.SCANNED


async def test_forbidden_message_fetch_is_recorded_as_permission_denied() -> None:
    """Represent non-quota Slack access denial as coverage instead of clean content."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = SlackAdapter(lambda config: slack_api(http), 1024)
        ref: ResourceRef = adapter._message_ref("T1", "C1", "1700000000.0")
        content: ResourceContent = await adapter.fetch(collector_config(), ref)

    assert not content.complete
    assert content.coverage is not None
    assert content.coverage.state is CoverageState.PERMISSION_DENIED
    assert content.placement[0].value == "C1"


async def test_forbidden_file_metadata_is_recorded_as_permission_denied() -> None:
    """Record denied file metadata reads as an unscanned resource."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = SlackAdapter(lambda config: slack_api(http), 1024)
        ref: ResourceRef = adapter._file_ref("T1", "F1")
        content: ResourceContent = await adapter.fetch(collector_config(), ref)

    assert not content.complete
    assert content.coverage is not None
    assert content.coverage.state is CoverageState.PERMISSION_DENIED


async def test_sweep_emits_context_for_messages_and_only_reads() -> None:
    """Populate streamed message context and keep the sweep on read-only endpoints."""
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method: str = request.url.path.rsplit("/", maxsplit=1)[-1]
        requests.append((request.method, method))
        if method == "auth.test":
            return httpx.Response(200, json={"ok": True, "team_id": "T1"})
        if method == "conversations.list":
            return httpx.Response(200, json={"ok": True, "channels": [{"id": "C1"}]})
        if method == "conversations.info":
            return httpx.Response(
                200, json={"ok": True, "channel": {"id": "C1", "is_private": True}}
            )
        if method == "conversations.history":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [{"ts": "1700000000.0", "text": "safe", "user": "U_PRIVATE"}],
                },
            )
        if method == "files.list":
            return httpx.Response(200, json={"ok": True, "files": []})
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api: SlackApi = slack_api(http)
        sweep: SlackSweep = SlackSweep(lambda config: api, 1024)
        page: PartitionPage = await sweep.partition(
            collector_config(), PartitionRequest("", None, collector_config().selector)
        )
        entries: list[ShardEntry] = [
            entry async for entry in sweep.shard_resources(collector_config(), page.shards[0])
        ]

    assert len(entries) == 1
    assert entries[0].kind is ShardEntryKind.FETCHED
    content: ResourceContent | None = entries[0].content
    assert content is not None
    assert content.exposure is not None
    assert content.exposure.visibility is Visibility.INTERNAL
    assert content.owner is not None and content.owner.value == "U_PRIVATE"
    assert "U_PRIVATE" not in repr(content)
    assert content.placement[0].value == "C1"
    assert content.modified_at == datetime.fromtimestamp(1700000000, UTC)
    assert content.coverage is not None and content.coverage.state is CoverageState.SCANNED
    assert all(method == "GET" for method, endpoint in requests)
    assert all(not endpoint.startswith(("chat.", "files.delete")) for _, endpoint in requests)
    assert requests.count(("GET", "conversations.info")) == 1
