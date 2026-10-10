"""Google estate partitioning and streamed shard outcome tests."""

import httpx

from telescreen.collectors.google.adapter import GoogleAdapter
from telescreen.collectors.google.admin import AdminApi
from telescreen.collectors.google.sweep import GoogleSweep
from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import CollectorKind, ErrorCode
from telescreen.ports.collectors import CollectorConfig, DriveSelector, RateLimited
from telescreen.ports.sweeps import (
    PartitionPage,
    PartitionRequest,
    ShardEntry,
    ShardEntryKind,
    ShardSpec,
)
from tests.collectors.google.support import (
    GoogleTestBundle,
    SweepClientFactory,
    adapters_for,
    drive_config,
    make_bundle,
)


async def test_domain_sweep_enumerates_each_owned_and_shared_drive_file_once() -> None:
    """Impersonated user and shared-drive corpora produce non-overlapping shards."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/admin/directory/v1/users/"):
            user_id: str = request.url.path.rsplit("/", maxsplit=1)[-1]
            user_email: str = {
                "user-a": "a@example.invalid",
                "user-b": "b@example.invalid",
                "user-c": "c@example.invalid",
            }[user_id]
            return httpx.Response(200, json={"id": user_id, "primaryEmail": user_email})
        if request.url.path.endswith("/admin/directory/v1/users"):
            return httpx.Response(
                200,
                json={
                    "users": [
                        {"id": "user-a", "primaryEmail": "a@example.invalid"},
                        {"id": "user-b", "primaryEmail": "b@example.invalid"},
                        {"id": "user-c", "primaryEmail": "c@example.invalid"},
                    ]
                },
            )
        if request.url.path.endswith("/drive/v3/drives"):
            return httpx.Response(200, json={"drives": [{"id": "shared-1", "name": "shared"}]})
        if request.url.path.endswith("/drive/v3/files"):
            corpus: str = request.url.params.get("corpora", "")
            if corpus == "user":
                if "access:a@example.invalid" not in request.headers.get("Authorization", ""):
                    return httpx.Response(200, json={"files": []})
                return httpx.Response(
                    200,
                    json={
                        "files": [
                            {"id": "owned-file", "name": "owned.txt", "mimeType": "text/plain"}
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": "shared-file",
                            "name": "shared.txt",
                            "mimeType": "text/plain",
                            "driveId": "shared-1",
                        }
                    ]
                },
            )
        return httpx.Response(500)

    bundle: GoogleTestBundle = make_bundle(handler)
    adapters: tuple[GoogleAdapter, GoogleAdapter, GoogleAdapter] = adapters_for(bundle)
    admin: AdminApi = AdminApi(bundle.client, bundle.auth, "admin@example.invalid")
    sweep: GoogleSweep = GoogleSweep(
        CollectorKind.GDRIVE, SweepClientFactory(bundle.drive, adapters), admin, 10
    )
    config: CollectorConfig = drive_config()
    request: PartitionRequest = PartitionRequest("", None, config.selector)
    try:
        root: PartitionPage = await sweep.partition(config, request)
        assert {item.corpus_key for item in root.sub_corpora} == {
            "user:user-a",
            "user:user-b",
            "user:user-c",
            "drive:shared-1",
        }
        all_shards: list[ShardSpec] = []
        for corpus in root.sub_corpora:
            page: PartitionPage = await sweep.partition(
                config,
                PartitionRequest(corpus.corpus_key, None, config.selector),
            )
            all_shards.extend(page.shards)
        file_ids: list[str] = []
        for shard in all_shards:
            descriptor: JsonValue = shard.descriptor
            files_value: JsonValue | None = descriptor.get("file_ids")
            assert files_value is not None
            values: tuple[JsonValue, ...] = files_value.as_list() or ()
            file_ids.extend(item.str_value for item in values)
            assert "@" not in repr(descriptor)
        assert sorted(file_ids) == ["owned-file", "shared-file"]
        assert len(file_ids) == len(set(file_ids))
    finally:
        await bundle.close()


async def test_shard_stream_classifies_fetched_removed_and_failed_resources() -> None:
    """A shard converts metadata 404 to removed, 403 to failed, and content to fetched."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/drive/v3/files"):
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": file_id,
                            "name": f"{file_id}.txt",
                            "mimeType": "text/plain",
                            "driveId": "drive-1",
                        }
                        for file_id in ("gone", "denied", "ok")
                    ]
                },
            )
        if request.url.path.endswith("/files/gone"):
            return httpx.Response(404, json={"message": "private"})
        if request.url.path.endswith("/files/denied"):
            return httpx.Response(403, json={"message": "private"})
        if request.url.path.endswith("/files/ok"):
            if request.url.params.get("alt") == "media":
                return httpx.Response(200, content=b"scanned transiently")
            return httpx.Response(
                200,
                json={
                    "id": "ok",
                    "name": "ok.txt",
                    "mimeType": "text/plain",
                    "driveId": "drive-1",
                },
            )
        return httpx.Response(500)

    bundle: GoogleTestBundle = make_bundle(handler)
    adapters: tuple[GoogleAdapter, GoogleAdapter, GoogleAdapter] = adapters_for(bundle)
    sweep: GoogleSweep = GoogleSweep(
        CollectorKind.GDRIVE, SweepClientFactory(bundle.drive, adapters), None, 10
    )
    config: CollectorConfig = drive_config(
        selector=DriveSelector(("drive-1",), (), ("text/plain",))
    )
    try:
        root: PartitionPage = await sweep.partition(
            config, PartitionRequest("", None, config.selector)
        )
        page: PartitionPage = await sweep.partition(
            config,
            PartitionRequest(root.sub_corpora[0].corpus_key, None, config.selector),
        )
        entries: list[ShardEntry] = [
            entry async for entry in sweep.shard_resources(config, page.shards[0])
        ]
        assert len(entries) == 3
        classifications: set[tuple[str, ShardEntryKind]] = {
            (entry.ref.raw_locator, entry.kind) for entry in entries
        }
        assert classifications == {
            ("gdrive:gone", ShardEntryKind.REMOVED),
            ("gdrive:denied", ShardEntryKind.FAILED),
            ("gdrive:ok", ShardEntryKind.FETCHED),
        }
        failed_entry: ShardEntry = next(
            entry for entry in entries if entry.ref.raw_locator == "gdrive:denied"
        )
        fetched_entry: ShardEntry = next(
            entry for entry in entries if entry.ref.raw_locator == "gdrive:ok"
        )
        assert failed_entry.error_code is ErrorCode.FORBIDDEN
        assert fetched_entry.content is not None and fetched_entry.content.complete
    finally:
        await bundle.close()


async def test_sweep_emits_size_skips_as_fetched_and_keeps_failures_failed() -> None:
    """Oversized media and exports are skipped, while export and decode errors fail."""
    drive_file_ids: tuple[str, ...] = ("big-drive", "bad-utf8")
    docs_file_ids: tuple[str, ...] = ("big-doc", "bad-export")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/drive/v3/files"):
            query: str = request.url.params.get("q", "")
            if "google-apps.document" in query:
                return httpx.Response(
                    200,
                    json={
                        "files": [
                            {
                                "id": file_id,
                                "name": file_id,
                                "mimeType": "application/vnd.google-apps.document",
                                "driveId": "drive-1",
                            }
                            for file_id in docs_file_ids
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": file_id,
                            "name": f"{file_id}.txt",
                            "mimeType": "text/plain",
                            "driveId": "drive-1",
                        }
                        for file_id in drive_file_ids
                    ]
                },
            )
        if request.url.path.endswith("/files/big-drive"):
            if request.url.params.get("alt") == "media":
                return httpx.Response(200, content=b"x" * 32)
            return httpx.Response(
                200,
                json={
                    "id": "big-drive",
                    "name": "big.txt",
                    "mimeType": "text/plain",
                    "driveId": "drive-1",
                },
            )
        if request.url.path.endswith("/files/bad-utf8"):
            if request.url.params.get("alt") == "media":
                return httpx.Response(200, content=b"\xff")
            return httpx.Response(
                200,
                json={
                    "id": "bad-utf8",
                    "name": "invalid.txt",
                    "mimeType": "text/plain",
                    "driveId": "drive-1",
                },
            )
        if request.url.path.endswith("/files/big-doc"):
            return httpx.Response(
                200,
                json={
                    "id": "big-doc",
                    "name": "big",
                    "mimeType": "application/vnd.google-apps.document",
                    "driveId": "drive-1",
                },
            )
        if request.url.path.endswith("/files/bad-export/export"):
            return httpx.Response(500, json={"message": "private"})
        if request.url.path.endswith("/files/bad-export"):
            return httpx.Response(
                200,
                json={
                    "id": "bad-export",
                    "name": "bad",
                    "mimeType": "application/vnd.google-apps.document",
                    "driveId": "drive-1",
                },
            )
        if request.url.path.endswith("/export"):
            return httpx.Response(200, content=b"x" * 32)
        return httpx.Response(500)

    bundle: GoogleTestBundle = make_bundle(handler)
    adapters: tuple[GoogleAdapter, GoogleAdapter, GoogleAdapter] = adapters_for(
        bundle, max_bytes=16
    )
    try:
        drive_sweep: GoogleSweep = GoogleSweep(
            CollectorKind.GDRIVE, SweepClientFactory(bundle.drive, adapters), None, 10
        )
        drive_config_value: CollectorConfig = drive_config(
            selector=DriveSelector(("drive-1",), (), ("text/plain",))
        )
        drive_root: PartitionPage = await drive_sweep.partition(
            drive_config_value, PartitionRequest("", None, drive_config_value.selector)
        )
        drive_page: PartitionPage = await drive_sweep.partition(
            drive_config_value,
            PartitionRequest(
                drive_root.sub_corpora[0].corpus_key, None, drive_config_value.selector
            ),
        )
        drive_entries: list[ShardEntry] = [
            entry
            async for entry in drive_sweep.shard_resources(drive_config_value, drive_page.shards[0])
        ]
        assert len(drive_entries) == len(drive_file_ids)
        drive_skipped: ShardEntry = next(
            entry for entry in drive_entries if entry.ref.raw_locator == "gdrive:big-drive"
        )
        drive_decode_failed: ShardEntry = next(
            entry for entry in drive_entries if entry.ref.raw_locator == "gdrive:bad-utf8"
        )
        assert drive_skipped.kind is ShardEntryKind.FETCHED
        assert drive_skipped.content is not None
        assert not drive_skipped.content.complete
        assert drive_skipped.content.segments == ()
        assert drive_skipped.content.sweep_covered
        assert drive_decode_failed.kind is ShardEntryKind.FAILED
        assert drive_decode_failed.error_code is ErrorCode.FETCH_FAILED

        docs_sweep: GoogleSweep = GoogleSweep(
            CollectorKind.GDOCS, SweepClientFactory(bundle.drive, adapters), None, 10
        )
        docs_config: CollectorConfig = drive_config(
            CollectorKind.GDOCS, DriveSelector(("drive-1",), (), ())
        )
        docs_root: PartitionPage = await docs_sweep.partition(
            docs_config, PartitionRequest("", None, docs_config.selector)
        )
        docs_page: PartitionPage = await docs_sweep.partition(
            docs_config,
            PartitionRequest(docs_root.sub_corpora[0].corpus_key, None, docs_config.selector),
        )
        docs_entries: list[ShardEntry] = [
            entry async for entry in docs_sweep.shard_resources(docs_config, docs_page.shards[0])
        ]
        assert len(docs_entries) == len(docs_file_ids)
        docs_skipped: ShardEntry = next(
            entry for entry in docs_entries if entry.ref.raw_locator == "gdocs:big-doc"
        )
        export_failed: ShardEntry = next(
            entry for entry in docs_entries if entry.ref.raw_locator == "gdocs:bad-export"
        )
        assert docs_skipped.kind is ShardEntryKind.FETCHED
        assert docs_skipped.content is not None
        assert not docs_skipped.content.complete
        assert docs_skipped.content.segments == ()
        assert docs_skipped.content.sweep_covered
        assert export_failed.kind is ShardEntryKind.FAILED
        assert export_failed.error_code is ErrorCode.FETCH_FAILED
    finally:
        await bundle.close()


async def test_sweep_propagates_rate_limit_from_content_fetch() -> None:
    """Drive rate limiting escapes the shard stream for map backpressure."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/drive/v3/files"):
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": "throttled",
                            "name": "throttled.txt",
                            "mimeType": "text/plain",
                            "driveId": "drive-1",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/files/throttled"):
            if request.url.params.get("alt") == "media":
                return httpx.Response(429, headers={"Retry-After": "2"})
            return httpx.Response(
                200,
                json={
                    "id": "throttled",
                    "name": "throttled.txt",
                    "mimeType": "text/plain",
                    "driveId": "drive-1",
                },
            )
        return httpx.Response(500)

    bundle: GoogleTestBundle = make_bundle(handler)
    adapters: tuple[GoogleAdapter, GoogleAdapter, GoogleAdapter] = adapters_for(bundle)
    sweep: GoogleSweep = GoogleSweep(
        CollectorKind.GDRIVE, SweepClientFactory(bundle.drive, adapters), None, 10
    )
    config: CollectorConfig = drive_config(
        selector=DriveSelector(("drive-1",), (), ("text/plain",))
    )
    try:
        root: PartitionPage = await sweep.partition(
            config, PartitionRequest("", None, config.selector)
        )
        page: PartitionPage = await sweep.partition(
            config,
            PartitionRequest(root.sub_corpora[0].corpus_key, None, config.selector),
        )
        try:
            await anext(sweep.shard_resources(config, page.shards[0]))
        except RateLimited as error:
            assert error.retry_after_seconds == 2.0
        else:
            raise AssertionError("rate-limited shard did not propagate RateLimited")
    finally:
        await bundle.close()
