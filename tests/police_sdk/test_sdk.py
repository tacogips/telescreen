"""Behavioral tests for the standalone Police SDK."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from examples.police.python.private_db_police import verify_task

from telescreen_police_sdk import _json
from telescreen_police_sdk.asgi import AsgiApp, create_webhook_app
from telescreen_police_sdk.client import BureauClient
from telescreen_police_sdk.masking import external_fingerprint, mask_value
from telescreen_police_sdk.models import (
    Callback,
    ClaimRequest,
    Manifest,
    MaskedFinding,
    Mode,
    Outcome,
    PartitionResult,
    Registration,
    ResultEnvelope,
    ResultError,
    ResultStatus,
    RuleClass,
    Shard,
    ShardStats,
    TaskEnvelope,
)
from telescreen_police_sdk.signing import NonceCache, VerifyResult, sign, signature_headers, verify
from telescreen_police_sdk.sweep import ResourceOutcome, SweepHandler
from telescreen_police_sdk.webhook import WebhookHandler, WebhookMode, WebhookReply, WebhookRequest
from telescreen_police_sdk.worker import PullWorker
from tests.support.samples import email_address, find_leaks


def _task(task_id: str = "task-a", callback: Callback | None = None) -> TaskEnvelope:
    """Return a safe task envelope used by SDK tests."""
    return TaskEnvelope(
        task_id,
        "verify_remediation",
        1,
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:05:00Z",
        (),
        _json.obj(()),
        callback,
    )


def _result(task_id: str) -> ResultEnvelope:
    """Return a successful result envelope."""
    return ResultEnvelope(task_id, ResultStatus.COMPLETED, outcome=Outcome.PASSED, summary="clean")


def _json_string(value: _json.Json | None, name: str) -> str | None:
    """Read an optional string property from a JSON object."""
    member: _json.Json | None = value.get(name) if value is not None else None
    return member.as_str() if member is not None else None


def test_json_model_is_canonical_and_rejects_nonstandard_numbers() -> None:
    """Canonical JSON sorts keys and rejects non-JSON numeric constants."""
    value: _json.Json = _json.parse('{"z":1,"a":[true,null]}')
    assert _json.dump(value) == '{"a":[true,null],"z":1}'
    with pytest.raises(ValueError, match="invalid JSON document"):
        _json.parse("NaN")


def test_contract_models_round_trip_and_omit_empty_optional_fields() -> None:
    """SDK DTOs preserve values and omit absent optional members."""
    manifest: Manifest = Manifest("worker", "1", Mode.PULL, ("detect",), (), 2)
    assert Manifest.from_json(manifest.to_json()) == manifest
    assert all(member.name != "endpoint_url" for member in manifest.to_json().members)
    claim: ClaimRequest = ClaimRequest(2, 10)
    assert ClaimRequest.from_json(claim.to_json()) == claim
    finding: MaskedFinding = MaskedFinding(
        "resource-id", "email", RuleClass.EMAIL, "s***[20]", "body", 0.9, "f" * 64
    )
    stats: ShardStats = ShardStats(1, 0, 32)
    result: ResultEnvelope = ResultEnvelope(
        "task-a",
        ResultStatus.COMPLETED,
        findings=(finding,),
        stats=stats,
        complete_locators=("resource-id",),
    )
    assert ResultEnvelope.from_json(result.to_json()) == result
    assert PartitionResult.from_json(
        PartitionResult((Shard("shard", _json.obj(()), 1),), "cursor", ("corpus",)).to_json()
    ) == PartitionResult((Shard("shard", _json.obj(()), 1),), "cursor", ("corpus",))
    failed: ResultEnvelope = ResultEnvelope(
        "task-b", ResultStatus.FAILED, error=ResultError("handler_error", "Task handler failed")
    )
    assert ResultEnvelope.from_json(failed.to_json()) == failed
    assert TaskEnvelope.from_json(
        _task(callback=Callback("https://example.invalid/callback", "token")).to_json()
    ) == _task(callback=Callback("https://example.invalid/callback", "token"))


def test_signing_detects_tamper_staleness_and_replay() -> None:
    """Signatures cover raw bytes and reject stale or reused nonces."""
    secret: bytes = b"test signing secret"
    body: bytes = b'{"task_id":"one"}'
    headers: tuple[tuple[str, str], ...] = signature_headers(secret, body, 1000, "nonce-a")
    cache: NonceCache = NonceCache()
    assert verify(headers, body, secret, 1000, cache) is VerifyResult.OK
    assert verify(headers, body, secret, 1000, cache) is VerifyResult.REPLAY
    assert verify(headers, body + b" ", secret, 1000, NonceCache()) is VerifyResult.BAD_SIGNATURE
    assert verify(headers, body, secret, 1301, NonceCache()) is VerifyResult.STALE
    assert verify((), body, secret, 1000, NonceCache()) is VerifyResult.MISSING
    assert sign(secret, body, 1000, "nonce-a").startswith("v1=")


def test_nonce_cache_expires_entries() -> None:
    """Nonce entries expire after their configured TTL."""
    cache: NonceCache = NonceCache(ttl_seconds=4)
    assert not cache.seen_or_add("n", 10)
    assert cache.seen_or_add("n", 11)
    assert not cache.seen_or_add("n", 14)


def test_masking_and_fingerprints_are_safe_and_stable() -> None:
    """Masking keeps only an optional prefix and fingerprints are keyed."""
    sample: str = email_address().value
    masked: str = mask_value(sample, keep_prefix=1)
    assert sample not in masked
    assert masked.startswith("s")
    assert len(external_fingerprint(sample, b"key")) == 64
    assert external_fingerprint(sample, b"key") != external_fingerprint(sample, b"other")


@pytest.mark.asyncio
async def test_bureau_client_uses_contract_paths_and_safe_errors() -> None:
    """Client operations use exact endpoint paths and error codes only."""
    requests: list[str] = []

    def route(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/register"):
            return httpx.Response(
                200,
                json={
                    "worker_id": "w",
                    "lease_seconds": 60,
                    "heartbeat_interval_seconds": 20,
                    "max_wait_seconds": 30,
                    "worker_token": "secret-token",
                },
            )
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json={"tasks": []})
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"cancelled_task_ids": ["task-x"]})
        if request.url.path.endswith("/result"):
            return httpx.Response(204)
        return httpx.Response(
            422, json={"error": {"code": "invalid_result", "detail": "do not echo"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
        client: BureauClient = BureauClient("https://bureau.example.invalid", http)
        registration: Registration = await client.register(
            Manifest("n", "1", Mode.PULL, ("detect",), (), 1), "enroll"
        )
        assert registration.worker_id == "w"
        assert await client.claim("w", "worker", ClaimRequest(1, 0)) == ()
        assert await client.heartbeat("w", "worker", ("task-x",)) == ("task-x",)
        await client.submit_result("worker", _result("task-x"))
        with pytest.raises(Exception) as captured:
            await client.callback(
                "https://bureau.example.invalid/callback", _result("task-x"), b"key"
            )
        assert "invalid_result" in str(captured.value)
        assert "do not echo" not in str(captured.value)
    assert requests[:4] == [
        "/api/police/v1/workers/register",
        "/api/police/v1/workers/w/claim",
        "/api/police/v1/workers/w/heartbeat",
        "/api/police/v1/tasks/task-x/result",
    ]


@pytest.mark.asyncio
async def test_pull_worker_limits_claims_and_cancels_heartbeat_task() -> None:
    """Worker claims two slots, then cancels a task listed by heartbeat."""
    heartbeat_seen: asyncio.Event = asyncio.Event()
    cancelled_seen: asyncio.Event = asyncio.Event()
    slot_claim_seen: asyncio.Event = asyncio.Event()
    submitted: list[str] = []
    active_handler_ids: set[str] = set()
    claim_requests: list[tuple[int, tuple[str, ...]]] = []
    cancelled_task_ids: set[str] = set()
    claims: int = 0

    def route(request: httpx.Request) -> httpx.Response:
        nonlocal claims
        if request.url.path.endswith("/claim"):
            claims += 1
            claim_json: _json.Json = _json.parse(request.content)
            claim_request: ClaimRequest = ClaimRequest.from_json(claim_json)
            claim_requests.append((claim_request.max_tasks, tuple(sorted(active_handler_ids))))
            if claims > 1:
                slot_claim_seen.set()
                third_task: bytes = _json.dump(_task("task-c").to_json()).encode()
                return httpx.Response(200, content=b'{"tasks":[' + third_task + b"]}")
            tasks: list[bytes] = [
                _json.dump(_task("task-a").to_json()).encode(),
                _json.dump(_task("task-b").to_json()).encode(),
            ]
            return httpx.Response(200, content=b'{"tasks":[' + b",".join(tasks) + b"]}")
        if request.url.path.endswith("/heartbeat"):
            heartbeat_seen.set()
            return httpx.Response(
                200,
                json={
                    "cancelled_task_ids": sorted(
                        task_id for task_id in active_handler_ids if task_id in ("task-a", "task-c")
                    )
                },
            )
        if request.url.path.endswith("/result"):
            result_value: _json.Json = _json.parse(request.content)
            task_id_value: _json.Json | None = result_value.get("task_id")
            parsed_task_id: str | None = (
                task_id_value.as_str() if task_id_value is not None else None
            )
            task_id: str = parsed_task_id or ""
            submitted.append(task_id)
            return httpx.Response(204)
        return httpx.Response(404)

    async def handler(task: TaskEnvelope) -> ResultEnvelope:
        active_handler_ids.add(task.task_id)
        try:
            if task.task_id in ("task-a", "task-c"):
                await asyncio.Event().wait()
            return _result(task.task_id)
        except asyncio.CancelledError:
            cancelled_task_ids.add(task.task_id)
            if {"task-a", "task-c"} <= cancelled_task_ids:
                cancelled_seen.set()
            raise
        finally:
            active_handler_ids.discard(task.task_id)

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
        worker: PullWorker = PullWorker(
            BureauClient("https://bureau.example.invalid", http),
            "worker",
            "token",
            (("verify_remediation", handler),),
            2,
            0.01,
            0,
        )
        stop: asyncio.Event = asyncio.Event()
        running: asyncio.Task[None] = asyncio.create_task(worker.run(stop))
        await asyncio.wait_for(slot_claim_seen.wait(), timeout=1)
        await asyncio.wait_for(heartbeat_seen.wait(), timeout=1)
        await asyncio.wait_for(cancelled_seen.wait(), timeout=1)
        stop.set()
        await asyncio.wait_for(running, timeout=1)
    assert submitted == ["task-b"]
    assert claim_requests[0] == (2, ())
    assert (1, ("task-a",)) in claim_requests
    assert all(max_tasks <= 2 - len(active_ids) for max_tasks, active_ids in claim_requests)
    assert all(max_tasks <= 1 for max_tasks, active_ids in claim_requests if "task-a" in active_ids)


@pytest.mark.asyncio
async def test_pull_worker_handler_exception_submits_static_handler_error() -> None:
    """A handler exception is reported without sending its raw message."""
    secret_sample: str = email_address().value
    result_bodies: list[bytes] = []
    claims: int = 0
    stop: asyncio.Event = asyncio.Event()

    def route(request: httpx.Request) -> httpx.Response:
        nonlocal claims
        if request.url.path.endswith("/claim"):
            claims += 1
            if claims > 1:
                return httpx.Response(200, json={"tasks": []})
            task_bytes: bytes = _json.dump(_task("task-raises").to_json()).encode()
            return httpx.Response(200, content=b'{"tasks":[' + task_bytes + b"]}")
        if request.url.path.endswith("/result"):
            result_bodies.append(request.content)
            stop.set()
            return httpx.Response(204)
        return httpx.Response(404)

    async def handler(task: TaskEnvelope) -> ResultEnvelope:
        raise RuntimeError(f"private data source rejected {secret_sample}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as http:
        worker: PullWorker = PullWorker(
            BureauClient("https://bureau.example.invalid", http),
            "worker",
            "token",
            (("verify_remediation", handler),),
            1,
            0.01,
            0,
        )
        running: asyncio.Task[None] = asyncio.create_task(worker.run(stop))
        await asyncio.wait_for(running, timeout=1)

    assert len(result_bodies) == 1
    result_body: bytes = result_bodies[0]
    result: ResultEnvelope = ResultEnvelope.from_json(_json.parse(result_body))
    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "handler_error"
    assert result.error.detail == "Task handler failed"
    assert secret_sample.encode() not in result_body


@pytest.mark.asyncio
async def test_webhook_sync_async_callback_and_bad_signature() -> None:
    """Webhook supports immediate results, signed callbacks and rejection."""
    callback_request: list[httpx.Request] = []

    def callback_route(request: httpx.Request) -> httpx.Response:
        callback_request.append(request)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(callback_route)) as http:
        client: BureauClient = BureauClient("https://bureau.example.invalid", http)

        async def handler(task: TaskEnvelope) -> ResultEnvelope:
            return _result(task.task_id)

        secret: bytes = b"shared secret"
        now: int = int(time.time())
        task: TaskEnvelope = _task(
            callback=Callback("https://bureau.example.invalid/callback", "one-time")
        )
        raw_body: bytes = _json.dump(task.to_json()).encode()
        good_headers: tuple[tuple[str, str], ...] = signature_headers(
            secret, raw_body, now, "push-nonce"
        )
        sync: WebhookHandler = WebhookHandler(
            (("verify_remediation", handler),), secret, clock=lambda: now
        )
        reply: WebhookReply = await sync.handle(WebhookRequest(good_headers, raw_body))
        assert reply.status == 200
        assert ResultEnvelope.from_json(_json.parse(reply.body)).task_id == task.task_id
        assert (await sync.handle(WebhookRequest(good_headers, raw_body))).status == 401

        async_handler: WebhookHandler = WebhookHandler(
            (("verify_remediation", handler),), secret, WebhookMode.ASYNC, client, lambda: now
        )
        async_reply: WebhookReply = await async_handler.handle(
            WebhookRequest(signature_headers(secret, raw_body, now, "push-nonce-2"), raw_body)
        )
        assert async_reply.status == 202
        await asyncio.gather(*async_handler._background)
        assert len(callback_request) == 1
        callback_headers: tuple[tuple[str, str], ...] = tuple(
            (name.decode(), value.decode()) for name, value in callback_request[0].headers.raw
        )
        assert (
            verify(callback_headers, callback_request[0].content, secret, now, NonceCache())
            is VerifyResult.OK
        )
        callback_result: ResultEnvelope = ResultEnvelope.from_json(
            _json.parse(callback_request[0].content)
        )
        assert callback_result.result_token == "one-time"
        bad: tuple[tuple[str, str], ...] = tuple(
            (key, "bad") if key == "X-Telescreen-Signature" else (key, value)
            for key, value in good_headers
        )
        assert (await sync.handle(WebhookRequest(bad, raw_body))).status == 401


@pytest.mark.asyncio
async def test_asgi_app_post_and_get() -> None:
    """ASGI adapter delegates POST and rejects GET."""

    async def handler(task: TaskEnvelope) -> ResultEnvelope:
        return _result(task.task_id)

    secret: bytes = b"asgi secret"
    task: TaskEnvelope = _task()
    body: bytes = _json.dump(task.to_json()).encode()
    signed: tuple[tuple[str, str], ...] = signature_headers(secret, body, 100, "asgi-nonce")
    webhook: WebhookHandler = WebhookHandler(
        (("verify_remediation", handler),), secret, clock=lambda: 100
    )
    app: AsgiApp = create_webhook_app(webhook)
    asgi_transport: httpx.AsyncBaseTransport = httpx.__dict__["ASGITransport"](app=app)
    async with httpx.AsyncClient(transport=asgi_transport, base_url="http://test") as http:
        response: httpx.Response = await http.post("/", content=body, headers=dict(signed))
        assert response.status_code == 200
        get_response: httpx.Response = await http.get("/")
        assert get_response.status_code == 405


@pytest.mark.asyncio
async def test_sweep_accounts_for_resources_and_limits_partition() -> None:
    """Shard scan counts failures and partition rejects more than one thousand shards."""

    async def partition(task: TaskEnvelope) -> PartitionResult:
        count: int = 1200 if task.task_id == "too-many" else 1
        return PartitionResult(tuple(Shard(str(index), _json.obj(()), 1) for index in range(count)))

    async def scan(_: _json.Json) -> AsyncIterator[ResourceOutcome]:
        yield ResourceOutcome("complete-a", (), True)
        yield ResourceOutcome("failed-b", (), False)
        raise RuntimeError("private error")

    sweep: SweepHandler = SweepHandler(partition, scan)
    page: ResultEnvelope = await sweep.partition(_task("ok"))
    assert page.status is ResultStatus.COMPLETED
    assert page.payload is not None
    assert PartitionResult.from_json(page.payload).shards
    too_many: ResultEnvelope = await sweep.partition(_task("too-many"))
    assert too_many.status is ResultStatus.FAILED
    shard: ResultEnvelope = await sweep.scan_shard(_task())
    assert shard.complete_locators == ("complete-a",)
    assert shard.stats == ShardStats(1, 2, 0)


@pytest.mark.asyncio
async def test_private_db_example_masks_values_and_rejects_identifiers(tmp_path: Path) -> None:
    """Private DB example reports masked data and safely rejects SQL identifiers."""
    database: Path = tmp_path / "private.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE "records" ("contact" TEXT)')
        connection.execute('INSERT INTO "records" VALUES (?)', (email_address().value,))
    input_value: _json.Json = _json.parse(
        '{"issue":{"display_locator":"sqlite://records/contact"}}'
    )
    task: TaskEnvelope = TaskEnvelope(
        "verify", "verify_remediation", 1, "now", "later", (), input_value
    )
    result: ResultEnvelope = await verify_task(task, str(database), b"fingerprint-key")
    serialized: str = _json.dump(result.to_json())
    assert result.outcome is Outcome.FAILED
    assert result.findings[0].masked_snippet != email_address().value
    assert email_address().value not in serialized
    assert not find_leaks(serialized)

    invalid_input: _json.Json = _json.parse(
        '{"issue":{"display_locator":"sqlite://records;DROP/contact"}}'
    )
    invalid: ResultEnvelope = await verify_task(
        TaskEnvelope("invalid", "verify_remediation", 1, "now", "later", (), invalid_input),
        str(database),
        b"fingerprint-key",
    )
    assert invalid.status is ResultStatus.FAILED
    with sqlite3.connect(database) as connection:
        names: tuple[str, ...] = tuple(
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        )
    assert names == ("records",)


def test_n8n_workflow_is_importable_shaped_json_without_secrets() -> None:
    """Example workflow contains required n8n node types and valid connections."""
    path: Path = Path("examples/police/n8n/telescreen-push-police.json")
    document: _json.Json = _json.parse(path.read_bytes())
    nodes_value: _json.Json | None = document.get("nodes")
    nodes: tuple[_json.Json, ...] | None = nodes_value.as_list() if nodes_value else None
    assert nodes is not None
    names: set[str] = {value for node in nodes if (value := _json_string(node, "name")) is not None}
    types: set[str] = {value for node in nodes if (value := _json_string(node, "type")) is not None}
    assert {
        "n8n-nodes-base.webhook",
        "n8n-nodes-base.crypto",
        "n8n-nodes-base.if",
        "n8n-nodes-base.respondToWebhook",
        "n8n-nodes-base.code",
        "n8n-nodes-base.httpRequest",
    } <= types
    connections: _json.Json | None = document.get("connections")
    assert connections is not None
    assert all(
        _json_string(target, "node") in names
        for connection in connections.members
        for main in (connection.value.get("main"),)
        if main is not None and main.as_list() is not None
        for branch in main.items
        for branch_items in (branch.as_list(),)
        if branch_items is not None
        for target in branch_items
        if target.get("node") is not None
    )
    assert not find_leaks(path.read_bytes().decode())
