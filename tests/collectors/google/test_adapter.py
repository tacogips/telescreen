"""Google webhook, scheduled changes, fetch, watch and HTTP error tests."""

import hashlib
import hmac
from datetime import timedelta

import httpx

from telescreen.collectors.google.adapter import GOOGLE_DOCUMENT, GoogleAdapter
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import CollectorKind
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    DriveSelector,
    EventCheck,
    EventCheckKind,
    InboundWebhook,
    RateLimited,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    WatchState,
)
from tests.collectors.google.support import (
    GoogleTestBundle,
    adapters_for,
    drive_config,
    make_bundle,
)
from tests.support.samples import Sample, email_address


async def test_event_verification_accepts_signed_channel_and_sync_without_work() -> None:
    """Drive HMACs use the domain separator and sync requests schedule no scan."""
    key: bytes = b"channel-secret"
    channel: str = "watch-1"
    token: str = hmac.new(key, b"drive-channel\0" + channel.encode(), hashlib.sha256).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle, key)[0]
    secrets: CollectorSecrets = CollectorSecrets(None, key)
    try:
        event: InboundWebhook = InboundWebhook(
            CollectorKind.GDRIVE,
            "POST",
            bundle.clock.now(),
            (("X-Goog-Channel-ID", channel), ("X-Goog-Channel-Token", token)),
            b"",
        )
        valid: EventCheck = await adapter.verify_event(event, secrets)
        assert valid.kind is EventCheckKind.VALID
        assert valid.config_hint == channel
        sync_event: InboundWebhook = InboundWebhook(
            CollectorKind.GDRIVE,
            "POST",
            bundle.clock.now(),
            (
                ("X-Goog-Channel-ID", channel),
                ("X-Goog-Channel-Token", token),
                ("X-Goog-Resource-State", "sync"),
            ),
            b"",
        )
        sync: EventCheck = await adapter.verify_event(sync_event, secrets)
        assert sync.kind is EventCheckKind.VALID
        assert sync.config_hint is None
        assert (await adapter.resources_from_event(sync_event, drive_config())).poll_changes
        bad_event: InboundWebhook = InboundWebhook(
            CollectorKind.GDRIVE,
            "POST",
            bundle.clock.now(),
            (("X-Goog-Channel-ID", channel), ("X-Goog-Channel-Token", "wrong")),
            b"",
        )
        assert (await adapter.verify_event(bad_event, secrets)).kind is EventCheckKind.INVALID
    finally:
        await bundle.close()


async def test_schedule_cursor_bootstrap_and_changes_page_classification() -> None:
    """Schedule polling bootstraps a cursor and classifies changed versus removed docs."""
    change_page: int = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal change_page
        if request.url.path.endswith("startPageToken"):
            return httpx.Response(200, json={"startPageToken": "cursor-start"})
        if request.url.path.endswith("/changes"):
            change_page += 1
            if change_page == 1:
                return httpx.Response(
                    200,
                    json={
                        "changes": [
                            {
                                "fileId": "trashed",
                                "file": {
                                    "id": "trashed",
                                    "name": "old",
                                    "mimeType": GOOGLE_DOCUMENT,
                                    "trashed": True,
                                    "parents": ["folder-1"],
                                },
                            },
                            {
                                "fileId": "outside",
                                "file": {
                                    "id": "outside",
                                    "name": "outside",
                                    "mimeType": GOOGLE_DOCUMENT,
                                    "parents": ["folder-other"],
                                },
                            },
                            {
                                "fileId": "doc-1",
                                "file": {
                                    "id": "doc-1",
                                    "name": "doc",
                                    "mimeType": GOOGLE_DOCUMENT,
                                    "parents": ["folder-1"],
                                },
                            },
                        ],
                        "nextPageToken": "cursor-next-page",
                    },
                )
            return httpx.Response(200, json={"newStartPageToken": "cursor-new-start"})
        return httpx.Response(500)

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle)[1]
    config: CollectorConfig = drive_config(
        CollectorKind.GDOCS,
        selector=DriveSelector((), ("folder-1",), ()),
    )
    try:
        initial: ChangePage = await adapter.list_changed(config, None)
        assert initial.changed == ()
        assert initial.next_cursor == CollectorCursor("cursor-start")
        first: ChangePage = await adapter.list_changed(config, initial.next_cursor)
        assert tuple(ref.ref_json for ref in first.changed) == ('{"file_id":"doc-1"}',)
        assert {ref.ref_json for ref in first.removed} == {
            '{"file_id":"outside"}',
            '{"file_id":"trashed"}',
        }
        assert first.has_more
        assert first.next_cursor == CollectorCursor("cursor-next-page")
        final: ChangePage = await adapter.list_changed(config, first.next_cursor)
        assert final.next_cursor == CollectorCursor("cursor-new-start")
        assert not final.has_more
    finally:
        await bundle.close()


async def test_doc_export_fetch_splits_lines_without_persisting_source_copy() -> None:
    """Docs export returns line segments and keeps the sample value repr-hidden."""
    sample: Sample = email_address()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/doc-1"):
            return httpx.Response(
                200,
                json={
                    "id": "doc-1",
                    "name": "notes",
                    "mimeType": GOOGLE_DOCUMENT,
                    "parents": ["folder-1"],
                },
            )
        if request.url.path.endswith("/export"):
            return httpx.Response(200, content=f"first\n{sample.value}".encode())
        return httpx.Response(500)

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle)[1]
    config: CollectorConfig = drive_config(CollectorKind.GDOCS)
    try:
        ref: ResourceRef = adapter.ref_from_json('{"file_id":"doc-1"}')
        content: ResourceContent = await adapter.fetch(config, ref)
        assert content.complete
        assert tuple(segment.locator for segment in content.segments) == ("L1", "L2")
        assert content.segments[1].text == sample.value
        assert sample.value not in repr(content)
    finally:
        await bundle.close()


async def test_sheets_fetch_returns_nonempty_cell_locators() -> None:
    """Spreadsheet fetch reads all sheet ranges and returns one segment per cell."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/sheet-1"):
            return httpx.Response(
                200,
                json={
                    "id": "sheet-1",
                    "name": "inventory",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                },
            )
        if request.url.path.endswith("/spreadsheets/sheet-1"):
            return httpx.Response(200, json={"sheets": [{"properties": {"title": "Sheet1"}}]})
        if request.url.path.endswith("/values:batchGet"):
            return httpx.Response(
                200,
                json={"valueRanges": [{"range": "'Sheet1'!A:ZZZ", "values": [["", "", "value"]]}]},
            )
        return httpx.Response(500)

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle)[2]
    try:
        ref: ResourceRef = adapter.ref_from_json('{"file_id":"sheet-1"}')
        content: ResourceContent = await adapter.fetch(drive_config(CollectorKind.GSHEETS), ref)
        assert content.complete
        assert tuple(segment.locator for segment in content.segments) == ("Sheet1!R1C3",)
        assert content.segments[0].text == "value"
        assert any(request.url.path.endswith("values:batchGet") for request in bundle.api_requests)
    finally:
        await bundle.close()


async def test_watch_renewal_stops_previous_channel_and_creates_seven_day_channel() -> None:
    """Renewal stops an old channel and posts a signed new channel and callback URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("startPageToken"):
            return httpx.Response(200, json={"startPageToken": "cursor"})
        if request.url.path.endswith("/channels/stop"):
            return httpx.Response(200)
        if request.url.path.endswith("/changes/watch"):
            return httpx.Response(
                200, json={"resourceId": "resource-2", "expiration": "1791550000000"}
            )
        return httpx.Response(500)

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle)[0]
    try:
        current: WatchState = WatchState("channel-old", "resource-old", bundle.clock.now())
        renewed: WatchState | None = await adapter.renew_watch(drive_config(), current)
        assert renewed is not None
        assert renewed.channel_id != "channel-old"
        assert renewed.resource_id == "resource-2"
        assert renewed.expires_at - bundle.clock.now() == timedelta(days=7)
        stop: httpx.Request = next(
            request for request in bundle.api_requests if request.url.path.endswith("channels/stop")
        )
        assert b'"resourceId":"resource-old"' in stop.content
        watch: httpx.Request = next(
            request for request in bundle.api_requests if request.url.path.endswith("changes/watch")
        )
        assert b"https://bureau.example.invalid/api/collectors/gdrive/webhook" in watch.content
    finally:
        await bundle.close()


async def test_drive_errors_map_not_found_and_rate_limit_without_echoing_body() -> None:
    """Drive 404 and 429 responses become stable safe collector exceptions."""

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "private resource detail"})

    bundle: GoogleTestBundle = make_bundle(missing)
    try:
        try:
            await bundle.drive.files_get_meta("missing")
        except ResourceGoneError as error:
            assert "private resource detail" not in str(error)
        else:
            raise AssertionError("404 was not classified as removed")
    finally:
        await bundle.close()

    def throttled(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "3"}, json={"message": "private"})

    second_bundle: GoogleTestBundle = make_bundle(throttled)
    try:
        try:
            await second_bundle.drive.files_get_meta("file-1")
        except RateLimited as error:
            assert error.retry_after_seconds == 3.0
            assert "private" not in str(error)
        else:
            raise AssertionError("429 was not classified as rate limited")
    finally:
        await second_bundle.close()


async def test_adapter_ref_json_is_strict_and_kind_prefixed() -> None:
    """Stored refs contain one file id and use their adapter kind in the locator."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    bundle: GoogleTestBundle = make_bundle(handler)
    adapter: GoogleAdapter = adapters_for(bundle)[0]
    ref: ResourceRef = adapter.ref_from_json('{"file_id":"file-1"}')
    assert ref.kind is CollectorKind.GDRIVE
    assert ref.raw_locator == "gdrive:file-1"
    try:
        adapter.ref_from_json('{"file_id":"file-1","title":"private"}')
    except TelescreenError as error:
        assert "private" not in str(error)
    else:
        raise AssertionError("extra ref properties were accepted")
    await bundle.close()
