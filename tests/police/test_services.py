"""Focused behavior tests for Police registration, signing, claims and results."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_null, parse_json
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import EngineTaskKind, ErrorCode, PoliceTaskStatus
from telescreen.db.models import PoliceTaskRow, PoliceWorkerRow
from telescreen.masking.redact import Redactor
from telescreen.police.claims import ClaimRequest, ClaimService
from telescreen.police.delivery import PolicePushDeliveryHandler
from telescreen.police.maintenance import PoliceMaintenance
from telescreen.police.registration import RegistrationService
from telescreen.police.results import ResultService
from telescreen.police.signing import SignatureHeaders, derive_push_secret, sign, verify_signature
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind
from tests.police.fakes import FakeConfigs, FakeIssues, FakeMapSink, FakeSignaler, RecordingAudit
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, github_token


def _registration_service(clock: FakeClock, audit: RecordingAudit) -> RegistrationService:
    """Build a service with synthetic enrollment and signing credentials."""
    return RegistrationService(
        SecretValue("enrollment-placeholder"),
        SecretValue("signing-placeholder"),
        False,
        audit,
        clock,
    )


@pytest.mark.asyncio
async def test_registration_issues_pull_token_and_rejects_http_push() -> None:
    """Pull registration stores only a token hash and insecure push fails closed."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: RecordingAudit = RecordingAudit()
    service: RegistrationService = _registration_service(clock, audit)
    pull_manifest: bytes = json.dumps(
        {
            "name": "local-police",
            "version": "1.0",
            "contract_version": "1.0",
            "mode": "pull",
            "task_types": ["verify_remediation"],
            "labels": ["region=west"],
            "concurrency": 2,
        }
    ).encode()
    async with harness.sessions.begin() as session:
        response: bytes = await service.register(session, pull_manifest)
    registration: JsonRegistration = JsonRegistration.from_bytes(response)
    assert registration.worker_token
    async with harness.sessions() as session:
        worker: PoliceWorkerRow | None = await session.get(PoliceWorkerRow, registration.worker_id)
        assert worker is not None
        assert worker.token_hash != registration.worker_token
        assert worker.mode == "pull"
    push_manifest: bytes = pull_manifest.replace(b'"pull"', b'"push"').replace(
        b'"concurrency": 2', b'"concurrency": 2, "endpoint_url": "http://worker.invalid/hook"'
    )
    with pytest.raises(TelescreenError) as error:
        async with harness.sessions.begin() as session:
            await service.register(session, push_manifest)
    assert error.value.code is ErrorCode.VALIDATION_ERROR
    assert len(audit.entries) == 1
    await harness.close()


@pytest.mark.asyncio
async def test_registration_rejects_unsupported_major_contract() -> None:
    """A valid schema-shaped non-v1 contract is rejected explicitly."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    service: RegistrationService = _registration_service(clock, RecordingAudit())
    manifest: bytes = (
        b'{"name":"local","version":"1","contract_version":"2.0",'
        b'"mode":"pull","task_types":["detect"],"labels":[],"concurrency":1}'
    )
    with pytest.raises(TelescreenError) as error:
        async with harness.sessions.begin() as session:
            await service.register(session, manifest)
    assert error.value.code is ErrorCode.UNSUPPORTED_CONTRACT_VERSION
    await harness.close()


def test_signing_uses_utf8_secret_and_rejects_stale_or_replayed_requests() -> None:
    """Bureau signing matches SDK semantics and checks freshness plus nonce state."""
    clock_now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
    secret: str = derive_push_secret(b"master", "worker-id")
    body: bytes = b'{"task_id":"task"}'
    timestamp: str = str(int(clock_now.timestamp()))
    nonce: str = "0123456789abcdef0123456789abcdef"
    headers: SignatureHeaders = SignatureHeaders(
        timestamp, nonce, sign(secret, body, timestamp, nonce)
    )
    assert verify_signature(headers, body, secret, clock_now, True)
    assert not verify_signature(headers, body, secret, clock_now, False)
    decoded_secret: bytes = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    raw_message: bytes = timestamp.encode() + b"." + nonce.encode() + b"." + body
    decoded_signature: str = (
        "v1=" + hmac.new(decoded_secret, raw_message, hashlib.sha256).hexdigest()
    )
    assert not verify_signature(
        SignatureHeaders(timestamp, nonce, decoded_signature), body, secret, clock_now, True
    )
    stale: SignatureHeaders = SignatureHeaders(
        str(int(clock_now.timestamp()) - 301), nonce, headers.signature
    )
    assert not verify_signature(stale, body, secret, clock_now, True)


@pytest.mark.asyncio
async def test_claim_attempts_and_lease_requeue_are_exact() -> None:
    """Attempts increment on claim but remain unchanged when maintenance requeues."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    worker: PoliceWorkerRow = PoliceWorkerRow(
        id="worker-id",
        name="unit",
        version="1",
        mode="pull",
        contract_version="1.0",
        task_types="verify_remediation",
        labels="region=west",
        concurrency=2,
        status="active",
        created_at=now,
    )
    task: PoliceTaskRow = PoliceTaskRow(
        id="task-id",
        task_type="verify_remediation",
        labels="region=west",
        input_json="{}",
        status="queued",
        worker_id=None,
        attempt=0,
        max_attempts=2,
        deadline=now + timedelta(hours=1),
        created_at=now,
    )
    async with harness.sessions.begin() as session:
        session.add_all((worker, task))
    claims: ClaimService = ClaimService(clock)
    leased: tuple[PoliceTaskRow, ...] = await claims.claim(
        harness.sessions, worker, ClaimRequest(1, 0)
    )
    assert len(leased) == 1 and leased[0].attempt == 1
    clock.advance(timedelta(seconds=61))
    sink: FakeMapSink = FakeMapSink()
    maintenance: PoliceMaintenance = PoliceMaintenance(sink, clock)
    async with harness.sessions.begin() as session:
        changed: int = await maintenance.tick(session, clock.now())
    assert changed == 1
    second_lease: tuple[PoliceTaskRow, ...] = await claims.claim(
        harness.sessions, worker, ClaimRequest(1, 0)
    )
    assert len(second_lease) == 1 and second_lease[0].attempt == 2
    clock.advance(timedelta(seconds=61))
    async with harness.sessions.begin() as session:
        changed = await maintenance.tick(session, clock.now())
    assert changed == 1
    async with harness.sessions() as session:
        stored: PoliceTaskRow | None = await session.get(PoliceTaskRow, "task-id")
        assert stored is not None
        assert stored.status == PoliceTaskStatus.FAILED.value
        assert stored.attempt == 2
    await harness.close()


@pytest.mark.asyncio
async def test_raw_result_rejection_preserves_task_state() -> None:
    """Raw values in a result raise 422 before any task mutation."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
    task: PoliceTaskRow = PoliceTaskRow(
        id="task-id",
        task_type="verify_remediation",
        labels="",
        input_json="{}",
        status="leased",
        worker_id="worker-id",
        attempt=1,
        max_attempts=3,
        deadline=now + timedelta(hours=1),
        created_at=now,
    )
    async with harness.sessions.begin() as session:
        session.add(task)
    audit: RecordingAudit = RecordingAudit()
    service: ResultService = ResultService(
        FakeIssues(),
        FakeConfigs(),
        FakeSignaler(),
        FakeMapSink(),
        Redactor.default(),
        audit,
        FakeClock(now),
    )
    sample: Sample = github_token()
    body: bytes = json.dumps(
        {
            "contract_version": "1.0",
            "task_id": "task-id",
            "status": "completed",
            "outcome": "passed",
            "summary": "found " + sample.value,
        }
    ).encode()
    with pytest.raises(TelescreenError) as error:
        async with harness.sessions.begin() as session:
            current: PoliceTaskRow | None = await session.get(PoliceTaskRow, "task-id")
            assert current is not None
            await service.accept(session, current, body)
    assert error.value.code is ErrorCode.RAW_VALUE_DETECTED
    async with harness.sessions() as session:
        current = await session.get(PoliceTaskRow, "task-id")
        assert current is not None and current.status == PoliceTaskStatus.LEASED.value
        assert current.result_json is None
    assert audit.entries
    assert sample.value not in repr(audit.entries)
    await harness.close()


@pytest.mark.asyncio
async def test_push_delivery_retries_with_backoff_then_exhausts() -> None:
    """Every POST increments one attempt and retry delay doubles until exhaustion."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
    clock: FakeClock = FakeClock(now)
    worker: PoliceWorkerRow = PoliceWorkerRow(
        id="push-worker",
        name="push",
        version="1",
        mode="push",
        contract_version="1.0",
        task_types="verify_remediation",
        labels="",
        concurrency=1,
        endpoint_url="https://worker.invalid/hook",
        status="active",
        created_at=now,
    )
    task: PoliceTaskRow = PoliceTaskRow(
        id="push-task",
        task_type="verify_remediation",
        labels="",
        input_json="{}",
        status="queued",
        worker_id=worker.id,
        attempt=0,
        max_attempts=3,
        deadline=now + timedelta(hours=1),
        created_at=now,
    )
    async with harness.sessions.begin() as session:
        session.add_all((worker, task))

    async def fail_post(request: httpx.Request) -> httpx.Response:
        """Simulate a deterministic remote 500 response."""
        del request
        return httpx.Response(500)

    client: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(fail_post))
    service: ResultService = ResultService(
        FakeIssues(),
        FakeConfigs(),
        FakeSignaler(),
        FakeMapSink(),
        Redactor.default(),
        RecordingAudit(),
        clock,
    )
    handler: PolicePushDeliveryHandler = PolicePushDeliveryHandler(
        harness.sessions,
        client,
        SecretValue("signing-placeholder"),
        "https://bureau.invalid",
        service,
        clock,
    )
    engine_task: ClaimedTask = ClaimedTask(
        "engine-id",
        EngineTaskKind.POLICE_PUSH_DELIVERY,
        task.id,
        json_null(),
        1,
        5,
        "police-push:push-task:1",
    )
    first: TaskResult = await handler.handle(engine_task)
    second: TaskResult = await handler.handle(engine_task)
    third: TaskResult = await handler.handle(engine_task)
    assert first.kind is TaskResultKind.RETRY
    assert first.available_at == now + timedelta(seconds=10)
    assert second.kind is TaskResultKind.RETRY
    assert second.available_at == now + timedelta(seconds=20)
    assert third.kind is TaskResultKind.DEAD
    async with harness.sessions() as session:
        stored: PoliceTaskRow | None = await session.get(PoliceTaskRow, task.id)
        assert stored is not None
        assert stored.attempt == 3 and stored.status == PoliceTaskStatus.FAILED.value
    await client.aclose()
    await harness.close()


@dataclass(frozen=True)
class JsonRegistration:
    """Small test-only view of a registration response."""

    worker_id: str
    worker_token: str | None

    @staticmethod
    def from_bytes(value: bytes) -> JsonRegistration:
        """Parse credential fields without retaining a native mapping."""
        parsed: JsonValue = parse_json(value)
        worker_id_value: JsonValue | None = parsed.get("worker_id")
        token_value: JsonValue | None = parsed.get("worker_token")
        return JsonRegistration(
            worker_id_value.as_str() or "" if worker_id_value is not None else "",
            token_value.as_str() if token_value is not None else None,
        )
