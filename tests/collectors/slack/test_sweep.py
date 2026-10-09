"""Tests for Slack conversation partitioning and shard resource streaming."""

import hashlib

import httpx

from telescreen.collectors.slack.client import SlackApi
from telescreen.collectors.slack.sweep import SlackSweep
from telescreen.core.json import json_str
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import CollectorKind, TriggerMode
from telescreen.ports.collectors import CollectorConfig, SlackSelector
from telescreen.ports.sweeps import (
    PartitionPage,
    PartitionRequest,
    ShardEntry,
    ShardEntryKind,
    SweepCapable,
)


def config() -> CollectorConfig:
    """Return a Slack workspace configuration used by sweep tests."""
    return CollectorConfig(
        id="collector-1",
        name="test-slack",
        kind=CollectorKind.SLACK,
        trigger_mode=TriggerMode.BOTH,
        cron="*/5 * * * *",
        sweep_cron=None,
        sweep_max_concurrency=4,
        selector=SlackSelector((), True),
        credential_ref="env:SLACK_BOT_TOKEN",
        enabled=True,
    )


async def test_partition_paginates_and_creates_deterministic_channel_shards() -> None:
    """Page public/private conversation listings and hash stable channel identities."""
    list_cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("auth.test"):
            return httpx.Response(200, json={"ok": True, "team_id": "T1"})
        if request.url.path.endswith("conversations.list"):
            cursor: str | None = request.url.params.get("cursor")
            list_cursors.append(cursor)
            if cursor is None:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "channels": [{"id": "C1"}],
                        "response_metadata": {"next_cursor": "page-2"},
                    },
                )
            return httpx.Response(
                200,
                json={"ok": True, "channels": [{"id": "C2"}]},
            )
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api: SlackApi = SlackApi(http, SecretValue("test-token"))
        sweep: SlackSweep = SlackSweep(lambda selected_config: api, 1024)
        first: PartitionPage = await sweep.partition(
            config(), PartitionRequest("", None, config().selector)
        )
        second: PartitionPage = await sweep.partition(
            config(), PartitionRequest("", first.next_cursor, config().selector)
        )
    assert list_cursors == [None, "page-2"]
    assert len(first.shards) == len(second.shards) == 1
    assert first.shards[0].shard_key == hashlib.sha256(b"slack:T1/C1").hexdigest()
    assert first.shards[0].descriptor.get("channel") == json_str("C1")
    assert second.shards[0].shard_key == hashlib.sha256(b"slack:T1/C2").hexdigest()
    assert first.next_cursor == "page-2"


async def test_shard_streams_message_and_failed_file_download() -> None:
    """Yield fetched message content and a safe failed file result on download error."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("auth.test"):
            return httpx.Response(200, json={"ok": True, "team_id": "T1"})
        if request.url.path.endswith("conversations.history"):
            return httpx.Response(
                200,
                json={"ok": True, "messages": [{"ts": "1700000000.000100", "text": "hello"}]},
            )
        if request.url.path.endswith("conversations.list"):
            return httpx.Response(200, json={"ok": True, "channels": [{"id": "C1"}]})
        if request.url.path.endswith("files.list"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "files": [
                        {
                            "id": "F1",
                            "name": "notes.txt",
                            "mimetype": "text/plain",
                            "size": 5,
                            "url_private": "https://files.slack.com/F1",
                            "created": 1700000000,
                            "timestamp": 1700000000,
                        }
                    ],
                },
            )
        if request.url.path.endswith("files.info"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "file": {
                        "id": "F1",
                        "name": "notes.txt",
                        "mimetype": "text/plain",
                        "size": 5,
                        "url_private": "https://files.slack.com/F1",
                        "created": 1700000000,
                        "timestamp": 1700000000,
                    },
                },
            )
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api: SlackApi = SlackApi(http, SecretValue("test-token"))
        sweep: SlackSweep = SlackSweep(lambda selected_config: api, 1024)
        sweep_contract: SweepCapable = sweep
        partition: PartitionPage = await sweep.partition(
            config(), PartitionRequest("", None, config().selector)
        )
        entries: list[ShardEntry] = [
            entry async for entry in sweep_contract.shard_resources(config(), partition.shards[0])
        ]
    assert [entry.kind for entry in entries] == [ShardEntryKind.FETCHED, ShardEntryKind.FAILED]
    assert entries[0].content is not None
    assert entries[0].content.segments[0].text == "hello"
    assert entries[1].error_code is not None
