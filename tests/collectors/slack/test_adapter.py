"""Tests for Slack webhook, schedule, and resource adapter behavior."""

import hashlib
import hmac
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest

from telescreen.collectors.slack.adapter import SlackAdapter
from telescreen.collectors.slack.client import SlackApi
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import CollectorKind, TriggerMode
from telescreen.ports.collectors import (
    ChangePage,
    CollectorAdapter,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    EventCheck,
    EventCheckKind,
    EventResources,
    InboundWebhook,
    RateLimited,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    SlackSelector,
)
from tests.support.samples import Sample, slack_token


@dataclass
class TrackingByteStream(httpx.AsyncByteStream):
    """Count response chunks consumed by the downloader."""

    chunks: tuple[bytes, ...]
    yielded_chunks: int = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.yielded_chunks += 1
            yield chunk

    async def aclose(self) -> None:
        return None


BASE_TIME: datetime = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def config(include_files: bool = True) -> CollectorConfig:
    """Return a selected Slack configuration for adapter tests."""
    return CollectorConfig(
        id="collector-1",
        name="test-slack",
        kind=CollectorKind.SLACK,
        trigger_mode=TriggerMode.BOTH,
        cron="*/5 * * * *",
        sweep_cron=None,
        sweep_max_concurrency=4,
        selector=SlackSelector(("C1",), include_files),
        credential_ref="env:SLACK_BOT_TOKEN",
        enabled=True,
    )


def signed_event(body: bytes, timestamp: str, received_at: datetime = BASE_TIME) -> InboundWebhook:
    """Create an exact-body Slack signature for a test webhook."""
    base: bytes = b"v0:" + timestamp.encode() + b":" + body
    signature: str = hmac.new(slack_token().value.encode(), base, hashlib.sha256).hexdigest()
    return InboundWebhook(
        CollectorKind.SLACK,
        "POST",
        received_at,
        (
            ("X-Slack-Request-Timestamp", timestamp),
            ("X-Slack-Signature", f"v0={signature}"),
        ),
        body,
    )


def adapter_for(http: httpx.AsyncClient) -> SlackAdapter:
    """Build an adapter that reuses the mocked Slack client."""
    api: SlackApi = SlackApi(http, SecretValue(slack_token().value))
    result: SlackAdapter = SlackAdapter(lambda selected_config: api, 1024)
    adapter_contract: CollectorAdapter = result
    assert adapter_contract.kind is CollectorKind.SLACK
    return result


@pytest.mark.parametrize(
    ("body", "timestamp", "received_at", "expected"),
    [
        (
            b'{"type":"event_callback","team_id":"T1"}',
            "1791460800",
            BASE_TIME,
            EventCheckKind.VALID,
        ),
        (
            b'{"type":"url_verification","challenge":"challenge-value"}',
            "1791460800",
            BASE_TIME,
            EventCheckKind.CHALLENGE,
        ),
        (b'{"type":"event_callback"}', "1791460499", BASE_TIME, EventCheckKind.INVALID),
    ],
)
async def test_verify_event_accepts_valid_challenge_and_old_signature(
    body: bytes, timestamp: str, received_at: datetime, expected: EventCheckKind
) -> None:
    """Accept current signatures, answer challenge, and reject a 301-second-old signature."""
    async with httpx.AsyncClient() as http:
        adapter: SlackAdapter = adapter_for(http)
        event: InboundWebhook = signed_event(body, timestamp, received_at)
        if expected is EventCheckKind.CHALLENGE:
            event = signed_event(body, "1791460800", BASE_TIME)
        result: EventCheck = await adapter.verify_event(
            event, CollectorSecrets(slack_token().value, None)
        )
    assert result.kind is expected
    if expected is EventCheckKind.CHALLENGE:
        assert result.response_body == "challenge-value"


async def test_tampered_event_signature_is_invalid() -> None:
    """Reject a signature computed for a different raw request body."""
    async with httpx.AsyncClient() as http:
        adapter: SlackAdapter = adapter_for(http)
        event: InboundWebhook = signed_event(b'{"type":"event_callback"}', "1791460800")
        tampered: InboundWebhook = InboundWebhook(
            event.kind,
            event.method,
            event.received_at,
            event.headers,
            b'{"type":"event_callback","tampered":true}',
        )
        result: EventCheck = await adapter.verify_event(
            tampered, CollectorSecrets(slack_token().value, None)
        )
    assert result.kind is EventCheckKind.INVALID


async def test_selected_message_and_deleted_events_make_refs() -> None:
    """Map selected channel message updates and deletions to stable locators."""
    async with httpx.AsyncClient() as http:
        adapter: SlackAdapter = adapter_for(http)
        changed: InboundWebhook = InboundWebhook(
            CollectorKind.SLACK,
            "POST",
            BASE_TIME,
            (),
            b'{"type":"event_callback","team_id":"T1","event":'
            b'{"type":"message","subtype":"message_changed","channel":"C1",'
            b'"ts":"1700000999.000900","message":{"ts":"1700000000.000100"}}}',
        )
        ignored: InboundWebhook = InboundWebhook(
            CollectorKind.SLACK,
            "POST",
            BASE_TIME,
            (),
            b'{"type":"event_callback","team_id":"T1","event":'
            b'{"type":"message","channel":"C2","ts":"1700000000.000100"}}',
        )
        deleted: InboundWebhook = InboundWebhook(
            CollectorKind.SLACK,
            "POST",
            BASE_TIME,
            (),
            b'{"type":"event_callback","team_id":"T1","event":'
            b'{"type":"message","subtype":"message_deleted","channel":"C1",'
            b'"ts":"1700000999.000901","deleted_ts":"1700000000.000200"}}',
        )
        updated: EventResources = await adapter.resources_from_event(changed, config())
        unselected: EventResources = await adapter.resources_from_event(ignored, config())
        removed: EventResources = await adapter.resources_from_event(deleted, config())
    assert len(updated.changed) == 1
    assert updated.changed[0].raw_locator == "slack:T1/C1/1700000000.000100"
    assert not unselected.changed
    assert not removed.changed
    assert removed.removed[0].raw_locator == "slack:T1/C1/1700000000.000200"


async def test_message_thread_events_preserve_thread_timestamp_in_refs() -> None:
    """Carry thread roots from ordinary and changed message events without changing locators."""
    async with httpx.AsyncClient() as http:
        adapter: SlackAdapter = adapter_for(http)
        ordinary: InboundWebhook = InboundWebhook(
            CollectorKind.SLACK,
            "POST",
            BASE_TIME,
            (),
            b'{"type":"event_callback","team_id":"T1","event":'
            b'{"type":"message","channel":"C1","ts":"1700000000.000200",'
            b'"thread_ts":"1700000000.000100"}}',
        )
        changed: InboundWebhook = InboundWebhook(
            CollectorKind.SLACK,
            "POST",
            BASE_TIME,
            (),
            b'{"type":"event_callback","team_id":"T1","event":'
            b'{"type":"message","subtype":"message_changed","channel":"C1",'
            b'"thread_ts":"ignored-parent","message":{"ts":"1700000000.000300",'
            b'"thread_ts":"1700000000.000100"}}}',
        )
        ordinary_refs: EventResources = await adapter.resources_from_event(ordinary, config())
        changed_refs: EventResources = await adapter.resources_from_event(changed, config())
        ordinary_ref: ResourceRef = ordinary_refs.changed[0]
        changed_ref: ResourceRef = changed_refs.changed[0]
        restored: ResourceRef = adapter.ref_from_json(ordinary_ref.ref_json)
        parent_ref: ResourceRef = adapter._message_ref(
            "T1", "C1", "1700000000.000100", "1700000000.000100"
        )
        restored_parent: ResourceRef = adapter.ref_from_json(parent_ref.ref_json)
    assert ordinary_ref.raw_locator == "slack:T1/C1/1700000000.000200"
    assert '"thread_ts":"1700000000.000100"' in ordinary_ref.ref_json
    assert changed_ref.raw_locator == "slack:T1/C1/1700000000.000300"
    assert '"thread_ts":"1700000000.000100"' in changed_ref.ref_json
    assert restored == ordinary_ref
    assert '"thread_ts"' not in parent_ref.ref_json
    assert restored_parent == parent_ref


async def test_file_event_respects_selector_and_ref_round_trip() -> None:
    """Ignore shared files when disabled and round-trip canonical file refs."""
    async with httpx.AsyncClient() as http:
        adapter: SlackAdapter = adapter_for(http)
        event: InboundWebhook = InboundWebhook(
            CollectorKind.SLACK,
            "POST",
            BASE_TIME,
            (),
            b'{"type":"event_callback","team_id":"T1","event":'
            b'{"type":"file_shared","channel_id":"C1","file_id":"F1"}}',
        )
        unselected_event: InboundWebhook = InboundWebhook(
            CollectorKind.SLACK,
            "POST",
            BASE_TIME,
            (),
            b'{"type":"event_callback","team_id":"T1","event":'
            b'{"type":"file_shared","channel_id":"C9","file_id":"F1"}}',
        )
        resources: EventResources = await adapter.resources_from_event(event, config())
        ignored: EventResources = await adapter.resources_from_event(event, config(False))
        unselected: EventResources = await adapter.resources_from_event(unselected_event, config())
        ref: ResourceRef = adapter._file_ref("T1", "F1")
        restored: ResourceRef = adapter.ref_from_json(ref.ref_json)
    assert resources.changed[0].raw_locator == "slack:T1/file/F1"
    assert not ignored.changed
    assert not unselected.changed
    assert restored == ref


async def test_fetch_hides_message_content_from_repr() -> None:
    """Fetch message text transiently and keep it out of ResourceContent repr."""
    sample: Sample = slack_token()
    expected_ts: str = "1700000000.000100"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("conversations.history"):
            if (
                request.url.params.get("inclusive") != "true"
                or request.url.params.get("oldest") != expected_ts
                or request.url.params.get("latest") != expected_ts
            ):
                return httpx.Response(200, json={"ok": True, "messages": []})
            return httpx.Response(
                200,
                json={"ok": True, "messages": [{"ts": expected_ts, "text": sample.value}]},
            )
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = adapter_for(http)
        ref: ResourceRef = adapter._message_ref("T1", "C1", expected_ts)
        content: ResourceContent = await adapter.fetch(config(), ref)
    assert content.segments[0].text == sample.value
    assert sample.value not in repr(content)


async def test_fetch_empty_inclusive_history_reports_gone() -> None:
    """Treat an inclusive exact-timestamp lookup with no message as genuinely gone."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("conversations.history"):
            assert request.url.params.get("inclusive") == "true"
            return httpx.Response(200, json={"ok": True, "messages": []})
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = adapter_for(http)
        ref: ResourceRef = adapter._message_ref("T1", "C1", "1700000000.000100")
        with pytest.raises(ResourceGoneError):
            await adapter.fetch(config(), ref)


async def test_fetch_thread_reply_uses_conversations_replies() -> None:
    """Fetch a thread reply by root timestamp while keeping its content out of repr."""
    sample: Sample = slack_token()
    reply_ts: str = "1700000000.000200"
    thread_ts: str = "1700000000.000100"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("conversations.history"):
            return httpx.Response(200, json={"ok": True, "messages": []})
        if request.url.path.endswith("conversations.replies"):
            assert request.url.params.get("channel") == "C1"
            assert request.url.params.get("ts") == thread_ts
            assert request.url.params.get("oldest") == reply_ts
            assert request.url.params.get("latest") == reply_ts
            assert request.url.params.get("inclusive") == "true"
            assert request.url.params.get("limit") == "2"
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [
                        {"ts": thread_ts, "text": "thread parent"},
                        {"ts": reply_ts, "text": sample.value},
                    ],
                },
            )
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = adapter_for(http)
        ref: ResourceRef = adapter._message_ref("T1", "C1", reply_ts, thread_ts)
        content: ResourceContent = await adapter.fetch(config(), ref)
    assert content.segments[0].text == sample.value
    assert sample.value not in repr(content)


async def test_fetch_thread_reply_parent_only_reports_gone() -> None:
    """Do not mistake a returned thread parent for the requested reply."""
    reply_ts: str = "1700000000.000200"
    thread_ts: str = "1700000000.000100"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("conversations.replies"):
            assert request.url.params.get("limit") == "2"
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [{"ts": thread_ts, "text": "thread parent"}],
                },
            )
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = adapter_for(http)
        ref: ResourceRef = adapter._message_ref("T1", "C1", reply_ts, thread_ts)
        with pytest.raises(ResourceGoneError):
            await adapter.fetch(config(), ref)


async def test_fetch_empty_thread_replies_reports_gone() -> None:
    """Report a thread reply as gone only when conversations.replies has no match."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("conversations.replies"):
            return httpx.Response(200, json={"ok": True, "messages": []})
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = adapter_for(http)
        ref: ResourceRef = adapter._message_ref(
            "T1", "C1", "1700000000.000200", "1700000000.000100"
        )
        with pytest.raises(ResourceGoneError):
            await adapter.fetch(config(), ref)


async def test_fetch_text_file_splits_lines_and_hides_repr() -> None:
    """Fetch a bounded UTF-8 Slack file into line segments without repr leaks."""
    sample: Sample = slack_token()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("files.info"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "file": {
                        "id": "F1",
                        "name": "private-notes.txt",
                        "mimetype": "text/plain",
                        "size": 1,
                        "url_private": "https://files.slack.com/F1",
                        "created": 1700000000,
                        "timestamp": 1700000000,
                    },
                },
            )
        if request.url.host == "files.slack.com":
            return httpx.Response(200, content=f"{sample.value}\nsecond line".encode())
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = adapter_for(http)
        ref: ResourceRef = adapter._file_ref("T1", "F1")
        content: ResourceContent = await adapter.fetch(config(), ref)
    assert content.complete
    assert tuple(segment.locator for segment in content.segments) == ("L1", "L2")
    assert content.segments[0].text == sample.value
    assert content.display_title == "Slack file"
    assert sample.value not in repr(content)
    assert "private-notes.txt" not in repr(content)


async def test_download_stops_after_limit_sentinel_byte() -> None:
    """Bound streamed Slack file data to the configured limit plus one byte."""
    stream: TrackingByteStream = TrackingByteStream((b"012", b"345", b"678", b"9"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "files.slack.com"
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api: SlackApi = SlackApi(http, SecretValue("test-token"))
        content: bytes = await api.download("https://files.slack.com/F1", 4)
    assert content == b"01234"
    assert stream.yielded_chunks == 2


async def test_fetch_deleted_message_and_rate_limited_file() -> None:
    """Map deleted messages to gone and preserve Slack retry-after on 429."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("conversations.history"):
            return httpx.Response(200, json={"ok": False, "error": "message_not_found"})
        return httpx.Response(429, headers={"Retry-After": "30"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = adapter_for(http)
        message_ref: ResourceRef = adapter._message_ref("T1", "C1", "1700000000.000100")
        with pytest.raises(ResourceGoneError):
            await adapter.fetch(config(), message_ref)
        file_ref: ResourceRef = adapter._file_ref("T1", "F1")
        with pytest.raises(RateLimited) as raised:
            await adapter.fetch(config(), file_ref)
    assert raised.value.retry_after_seconds == 30.0


async def test_schedule_pages_history_and_advances_decimal_cursor() -> None:
    """Page schedule history and advance the per-channel watermark exactly."""
    requests: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("auth.test"):
            return httpx.Response(200, json={"ok": True, "team_id": "T1"})
        if request.url.path.endswith("conversations.history"):
            cursor: str | None = request.url.params.get("cursor")
            requests.append(cursor)
            if cursor is None:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "messages": [{"ts": "1700000000.000100", "text": "one"}],
                        "response_metadata": {"next_cursor": "next"},
                    },
                )
            return httpx.Response(
                200,
                json={"ok": True, "messages": [{"ts": "1700000000.000200", "text": "two"}]},
            )
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = adapter_for(http)
        page: ChangePage = await adapter.list_changed(config(False), None)
    assert len(page.changed) == 2
    assert requests == [None, "next"]
    assert page.next_cursor == CollectorCursor('{"C1":"1700000000.000200"}')
    assert not page.full_listing


async def test_schedule_files_accept_integer_timestamp_and_advance_cursor() -> None:
    """Include real-shaped file timestamps in schedule refs and the cursor watermark."""
    file_ts: int = 1700000001

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("auth.test"):
            return httpx.Response(200, json={"ok": True, "team_id": "T1"})
        if request.url.path.endswith("conversations.history"):
            return httpx.Response(
                200,
                json={"ok": True, "messages": [{"ts": "1700000000.000100", "text": "one"}]},
            )
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
                            "created": file_ts,
                            "timestamp": file_ts,
                        }
                    ],
                },
            )
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter: SlackAdapter = adapter_for(http)
        page: ChangePage = await adapter.list_changed(config(True), None)
    assert tuple(ref.raw_locator for ref in page.changed) == (
        "slack:T1/C1/1700000000.000100",
        "slack:T1/file/F1",
    )
    assert page.next_cursor == CollectorCursor('{"C1":"1700000001"}')


async def test_verify_event_rejects_missing_secret() -> None:
    """Reject webhook validation when the configured signing secret is unavailable."""
    async with httpx.AsyncClient() as http:
        adapter: SlackAdapter = adapter_for(http)
        result: EventCheck = await adapter.verify_event(
            signed_event(b'{"type":"event_callback"}', "1791460800"),
            CollectorSecrets(None, None),
        )
    assert result.kind is EventCheckKind.INVALID
