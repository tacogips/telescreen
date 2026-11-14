"""SDK-driven Police v1 pull and push contract checks against the Bureau router."""

import secrets
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Request, Response
from sqlalchemy import select

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.police import PoliceRouterDeps, build_police_router
from telescreen.audit.service import AuditLogService
from telescreen.contracts.validator import validate
from telescreen.core.clock import FakeClock
from telescreen.core.json import json_null, json_object
from telescreen.core.secrets import SecretValue
from telescreen.db.models import AuditRecordRow, PoliceTaskRow, PoliceWorkerRow
from telescreen.db.replay import ReplayNonceStore
from telescreen.masking.redact import Redactor
from telescreen.police.claims import ClaimService
from telescreen.police.delivery import PolicePushDeliveryHandler
from telescreen.police.gateway import PullQueueTransport, RoutingPoliceGateway, WebhookTransport
from telescreen.police.registration import RegistrationService, hash_token
from telescreen.police.results import ResultService
from telescreen.police.signing import derive_push_secret, sign
from telescreen.ports.police import PoliceTaskRequest
from telescreen.ports.workflow import ClaimedTask, EngineTaskKind, TaskResult
from telescreen_police_sdk import _json
from telescreen_police_sdk.client import BureauClient
from telescreen_police_sdk.models import (
    ClaimRequest,
    Manifest,
    Mode,
    Outcome,
    Registration,
    ResultEnvelope,
    ResultStatus,
    TaskEnvelope,
)
from telescreen_police_sdk.webhook import (
    WebhookHandler,
    WebhookMode,
    WebhookReply,
    WebhookRequest,
)
from tests.police.fakes import (
    FakeConfigs,
    FakeIssues,
    FakeMapSink,
    FakeQueue,
    FakeSignaler,
    RecordingAudit,
)
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import github_token


def _result_service(clock: FakeClock, audit: RecordingAudit) -> ResultService:
    """Construct a result service wired to offline in-memory ports."""
    return ResultService(
        FakeIssues(), FakeConfigs(), FakeSignaler(), FakeMapSink(), Redactor.default(), audit, clock
    )


def _app(
    harness: SqliteHarness, clock: FakeClock, signing_key: SecretValue
) -> tuple[FastAPI, ResultService, RecordingAudit]:
    """Build an in-process API app and return its result service and audit fake."""
    audit: RecordingAudit = RecordingAudit()
    ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
    results: ResultService = _result_service(clock, audit)
    registration: RegistrationService = RegistrationService(
        SecretValue("enrollment-placeholder"), signing_key, False, audit, clock
    )
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(
        build_police_router(
            PoliceRouterDeps(
                ctx,
                registration,
                ClaimService(clock),
                results,
                ReplayNonceStore(),
                signing_key,
            )
        )
    )
    return app, results, audit


@pytest.mark.asyncio
async def test_sdk_pull_cycle_registers_claims_heartbeats_and_submits_result() -> None:
    """Drive the complete pull contract with the standalone SDK client."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    signing_key: SecretValue = SecretValue("signing-placeholder")
    app: FastAPI
    app, _, _ = _app(harness, clock, signing_key)
    transport: ASGITransport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client: BureauClient = BureauClient("http://test", http)
        registration: Registration = await client.register(
            Manifest("sdk-pull", "1.0", Mode.PULL, ("verify_remediation",), (), 2),
            "enrollment-placeholder",
        )
        assert registration.worker_token is not None
        response: httpx.Response = await http.get("/api/police/v1/openapi.json")
        assert response.status_code == 200
        request: PoliceTaskRequest = PoliceTaskRequest(
            "verify_remediation", (), json_object(()), clock.now() + timedelta(hours=1)
        )
        gateway: RoutingPoliceGateway = RoutingPoliceGateway(
            PullQueueTransport(), WebhookTransport(FakeQueue(), clock), clock
        )
        async with harness.sessions.begin() as session:
            task_id: str = await gateway.dispatch(session, request)
        tasks: tuple[TaskEnvelope, ...] = await client.claim(
            registration.worker_id, registration.worker_token, ClaimRequest(1, 0)
        )
        assert len(tasks) == 1
        assert tasks[0].task_id == task_id and tasks[0].attempt == 1
        assert (
            await client.heartbeat(registration.worker_id, registration.worker_token, (task_id,))
            == ()
        )
        result: ResultEnvelope = ResultEnvelope(
            task_id, ResultStatus.COMPLETED, Outcome.PASSED, summary="verified"
        )
        await client.submit_result(registration.worker_token, result)
        result_bytes: bytes = _json.dump(result.to_json()).encode()
        assert validate("result-envelope.schema.json", result_bytes) == []
        async with harness.sessions() as session:
            task: PoliceTaskRow | None = await session.get(PoliceTaskRow, task_id)
            assert task is not None and task.status == "completed"
    await harness.close()


@pytest.mark.asyncio
async def test_sdk_push_handler_accepts_bureau_signature_and_sync_result() -> None:
    """Forward a Bureau POST to the standalone SDK webhook through MockTransport."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    signing_key: SecretValue = SecretValue("signing-placeholder")
    app: FastAPI
    result_service: ResultService
    app, result_service, _ = _app(harness, clock, signing_key)

    async def police_handler(envelope: TaskEnvelope) -> ResultEnvelope:
        """Return a safe synchronous verification result."""
        return ResultEnvelope(envelope.task_id, ResultStatus.COMPLETED, Outcome.PASSED)

    webhook: WebhookHandler | None = None

    async def forward(request: Request) -> Response:
        """Pass request bytes and signed headers to the SDK webhook helper."""
        if webhook is None:
            return Response(status_code=500)
        reply: WebhookReply = await webhook.handle(
            WebhookRequest(tuple(request.headers.multi_items()), await request.aread())
        )
        return Response(status_code=reply.status, content=reply.body, headers=reply.headers)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bureau_http:
        client: BureauClient = BureauClient("http://test", bureau_http)
        registration: Registration = await client.register(
            Manifest(
                "sdk-push",
                "1.0",
                Mode.PUSH,
                ("verify_remediation",),
                (),
                1,
                "https://police.invalid/hook",
            ),
            "enrollment-placeholder",
        )
        assert registration.signing_secret is not None
        webhook = WebhookHandler(
            (("verify_remediation", police_handler),),
            registration.signing_secret.encode("utf-8"),
            WebhookMode.SYNC,
            clock=lambda: int(clock.now().timestamp()),
        )
        push_http: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(forward))
        queue: FakeQueue = FakeQueue()
        gateway: RoutingPoliceGateway = RoutingPoliceGateway(
            PullQueueTransport(), WebhookTransport(queue, clock), clock
        )
        async with harness.sessions.begin() as session:
            task_id: str = await gateway.dispatch(
                session,
                PoliceTaskRequest(
                    "verify_remediation", (), json_object(()), clock.now() + timedelta(hours=1)
                ),
            )
        async with harness.sessions() as session:
            dispatched: PoliceTaskRow | None = await session.get(PoliceTaskRow, task_id)
            assert dispatched is not None and dispatched.attempt == 0
        handler: PolicePushDeliveryHandler = PolicePushDeliveryHandler(
            harness.sessions,
            push_http,
            signing_key,
            "https://bureau.invalid",
            result_service,
            clock,
        )
        engine_task: ClaimedTask = ClaimedTask(
            "engine-task",
            EngineTaskKind.POLICE_PUSH_DELIVERY,
            task_id,
            json_null(),
            1,
            5,
            "police-push:" + task_id + ":1",
        )
        disposition: TaskResult = await handler.handle(engine_task)
        assert disposition.kind.value == "done"
        async with harness.sessions() as session:
            task: PoliceTaskRow | None = await session.get(PoliceTaskRow, task_id)
            assert task is not None and task.status == "completed"
            assert task.attempt == 1
        await push_http.aclose()
    await harness.close()


@pytest.mark.asyncio
async def test_signed_callback_rejects_nonce_replay_and_consumed_token() -> None:
    """A callback token is one-use and a valid nonce cannot be replayed."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    signing_key: SecretValue = SecretValue("signing-placeholder")
    app: FastAPI
    app, _, _ = _app(harness, clock, signing_key)
    transport: ASGITransport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client: BureauClient = BureauClient("http://test", http)
        registration: Registration = await client.register(
            Manifest(
                "callback-worker",
                "1.0",
                Mode.PUSH,
                ("verify_remediation",),
                (),
                1,
                "https://police.invalid/hook",
            ),
            "enrollment-placeholder",
        )
        assert registration.signing_secret is not None
        token_one: str = secrets.token_urlsafe(32)
        token_two: str = secrets.token_urlsafe(32)
        first_task: PoliceTaskRow = PoliceTaskRow(
            id="callback-task-one",
            task_type="verify_remediation",
            labels="",
            input_json="{}",
            status="delivered",
            worker_id=registration.worker_id,
            attempt=1,
            max_attempts=3,
            deadline=clock.now() + timedelta(hours=1),
            callback_token_hash=hash_token(token_one),
            created_at=clock.now(),
        )
        second_task: PoliceTaskRow = PoliceTaskRow(
            id="callback-task-two",
            task_type="verify_remediation",
            labels="",
            input_json="{}",
            status="delivered",
            worker_id=registration.worker_id,
            attempt=1,
            max_attempts=3,
            deadline=clock.now() + timedelta(hours=1),
            callback_token_hash=hash_token(token_two),
            created_at=clock.now(),
        )
        async with harness.sessions.begin() as session:
            session.add_all((first_task, second_task))

        timestamp: str = str(int(clock.now().timestamp()))
        nonce: str = "0123456789abcdef0123456789abcdef"
        secret: str = derive_push_secret(signing_key.value.encode("utf-8"), registration.worker_id)

        def callback_body(task_id: str, token: str) -> bytes:
            """Serialize one schema-valid SDK callback envelope."""
            sdk_result: ResultEnvelope = ResultEnvelope(
                task_id, ResultStatus.COMPLETED, Outcome.PASSED, result_token=token
            )
            return _json.dump(sdk_result.to_json()).encode()

        first_body: bytes = callback_body(first_task.id, token_one)
        first_headers: tuple[tuple[str, str], ...] = (
            ("X-Telescreen-Timestamp", timestamp),
            ("X-Telescreen-Nonce", nonce),
            ("X-Telescreen-Signature", sign(secret, first_body, timestamp, nonce)),
            ("Content-Type", "application/json"),
        )
        accepted: httpx.Response = await http.post(
            f"/api/police/v1/callbacks/{first_task.id}", content=first_body, headers=first_headers
        )
        assert accepted.status_code == 204
        reused_token_body: bytes = callback_body(first_task.id, token_one)
        fresh_nonce: str = "fedcba9876543210fedcba9876543210"
        reused_token_headers: tuple[tuple[str, str], ...] = (
            ("X-Telescreen-Timestamp", timestamp),
            ("X-Telescreen-Nonce", fresh_nonce),
            (
                "X-Telescreen-Signature",
                sign(secret, reused_token_body, timestamp, fresh_nonce),
            ),
        )
        reused: httpx.Response = await http.post(
            f"/api/police/v1/callbacks/{first_task.id}",
            content=reused_token_body,
            headers=reused_token_headers,
        )
        assert reused.status_code == 409
        second_body: bytes = callback_body(second_task.id, token_two)
        replay_headers: tuple[tuple[str, str], ...] = (
            ("X-Telescreen-Timestamp", timestamp),
            ("X-Telescreen-Nonce", nonce),
            ("X-Telescreen-Signature", sign(secret, second_body, timestamp, nonce)),
        )
        replay: httpx.Response = await http.post(
            f"/api/police/v1/callbacks/{second_task.id}",
            content=second_body,
            headers=replay_headers,
        )
        assert replay.status_code == 401
        async with harness.sessions() as session:
            stored: PoliceTaskRow | None = await session.get(PoliceTaskRow, first_task.id)
            assert stored is not None and stored.result_json is not None
            assert token_one not in stored.result_json
    await harness.close()


@pytest.mark.asyncio
async def test_raw_pull_result_commits_rejection_audit_and_leaves_task_open() -> None:
    """Commit the safe rejection audit while leaving raw results unpersisted."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditLogService = AuditLogService(clock, Redactor.default())
    signing_key: SecretValue = SecretValue("signing-placeholder")
    token: str = "pull-worker-token-placeholder"
    worker: PoliceWorkerRow = PoliceWorkerRow(
        id="audit-test-worker",
        name="audit-test-worker",
        version="1.0",
        mode="pull",
        contract_version="1.0",
        task_types="verify_remediation",
        labels="",
        concurrency=1,
        token_hash=hash_token(token),
        status="active",
        created_at=clock.now(),
    )
    task: PoliceTaskRow = PoliceTaskRow(
        id="audit-test-task",
        task_type="verify_remediation",
        labels="",
        input_json="{}",
        status="leased",
        worker_id=worker.id,
        attempt=1,
        max_attempts=3,
        deadline=clock.now() + timedelta(hours=1),
        created_at=clock.now(),
    )
    async with harness.sessions.begin() as session:
        session.add_all((worker, task))
    ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
    results: ResultService = ResultService(
        FakeIssues(), FakeConfigs(), FakeSignaler(), FakeMapSink(), Redactor.default(), audit, clock
    )
    registration: RegistrationService = RegistrationService(
        SecretValue("enrollment-placeholder"), signing_key, False, audit, clock
    )
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(
        build_police_router(
            PoliceRouterDeps(
                ctx,
                registration,
                ClaimService(clock),
                results,
                ReplayNonceStore(),
                signing_key,
            )
        )
    )
    sample: str = github_token().value
    envelope: ResultEnvelope = ResultEnvelope(
        task.id, ResultStatus.COMPLETED, Outcome.PASSED, summary=sample
    )
    body: bytes = _json.dump(envelope.to_json()).encode("utf-8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response: httpx.Response = await http.post(
            f"/api/police/v1/tasks/{task.id}/result",
            content=body,
            headers={"Authorization": "Bearer " + token},
        )
    assert response.status_code == 422
    async with harness.sessions() as session:
        stored_task: PoliceTaskRow | None = await session.get(PoliceTaskRow, task.id)
        records: tuple[AuditRecordRow, ...] = tuple(await session.scalars(select(AuditRecordRow)))
        assert stored_task is not None
        assert stored_task.status == "leased" and stored_task.result_json is None
        assert len(records) == 1 and records[0].action == "police.result_rejected"
        assert sample not in records[0].summary and sample not in records[0].details_json
    await harness.close()


@pytest.mark.asyncio
async def test_wrong_enrollment_cross_worker_and_disabled_worker_tokens_return_401() -> None:
    """Enrollment and worker credentials cannot cross worker or disabled boundaries."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    signing_key: SecretValue = SecretValue("signing-placeholder")
    app: FastAPI
    app, _, _ = _app(harness, clock, signing_key)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        bad_enrollment: httpx.Response = await http.post(
            "/api/police/v1/workers/register",
            content=(
                b'{"name":"unauthorized","version":"1.0","contract_version":"1.0",'
                b'"mode":"pull","task_types":["verify_remediation"],"labels":[],"concurrency":1}'
            ),
            headers={"Authorization": "Bearer wrong-enrollment-placeholder"},
        )
        assert bad_enrollment.status_code == 401
        assert "wrong-enrollment-placeholder" not in bad_enrollment.text
        client: BureauClient = BureauClient("http://test", http)
        first: Registration = await client.register(
            Manifest("worker-one", "1.0", Mode.PULL, ("verify_remediation",), (), 1),
            "enrollment-placeholder",
        )
        second: Registration = await client.register(
            Manifest("worker-two", "1.0", Mode.PULL, ("verify_remediation",), (), 1),
            "enrollment-placeholder",
        )
        assert first.worker_token is not None and second.worker_token is not None
        claim_body: bytes = b'{"max_tasks":1,"wait_seconds":0,"task_types":[]}'
        cross_worker: httpx.Response = await http.post(
            f"/api/police/v1/workers/{first.worker_id}/claim",
            content=claim_body,
            headers={"Authorization": "Bearer " + second.worker_token},
        )
        assert cross_worker.status_code == 401
        assert second.worker_token not in cross_worker.text
        async with harness.sessions.begin() as session:
            disabled: PoliceWorkerRow | None = await session.get(PoliceWorkerRow, first.worker_id)
            assert disabled is not None
            disabled.status = "disabled"
        disabled_worker: httpx.Response = await http.post(
            f"/api/police/v1/workers/{first.worker_id}/claim",
            content=claim_body,
            headers={"Authorization": "Bearer " + first.worker_token},
        )
        assert disabled_worker.status_code == 401
        assert first.worker_token not in disabled_worker.text
    await harness.close()


@pytest.mark.asyncio
async def test_partition_result_with_one_thousand_one_shards_returns_422() -> None:
    """The HTTP result route rejects oversized partition pages without leasing changes."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    signing_key: SecretValue = SecretValue("signing-placeholder")
    app: FastAPI
    app, _, _ = _app(harness, clock, signing_key)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        client: BureauClient = BureauClient("http://test", http)
        registration: Registration = await client.register(
            Manifest("partition-worker", "1.0", Mode.PULL, ("partition",), (), 1),
            "enrollment-placeholder",
        )
        assert registration.worker_token is not None
        task: PoliceTaskRow = PoliceTaskRow(
            id="oversized-partition",
            task_type="partition",
            labels="",
            input_json="{}",
            status="leased",
            worker_id=registration.worker_id,
            attempt=1,
            max_attempts=3,
            deadline=clock.now() + timedelta(hours=1),
            created_at=clock.now(),
        )
        async with harness.sessions.begin() as session:
            session.add(task)
        shard_items: str = ",".join(
            '{"shard_key":"shard-' + str(index) + '","descriptor":{},"weight":1}'
            for index in range(1001)
        )
        body: bytes = (
            '{"contract_version":"1.0","task_id":"oversized-partition",'
            '"status":"completed","payload":{"shards":['
            + shard_items
            + '],"next_cursor":null,"sub_corpora":[]}}'
        ).encode()
        rejected: httpx.Response = await http.post(
            f"/api/police/v1/tasks/{task.id}/result",
            content=body,
            headers={"Authorization": "Bearer " + registration.worker_token},
        )
        assert rejected.status_code == 422
        assert rejected.text == (
            '{"error":{"code":"validation_error","detail":"invalid partition result"}}'
        )
        async with harness.sessions() as session:
            stored: PoliceTaskRow | None = await session.get(PoliceTaskRow, task.id)
            assert stored is not None and stored.status == "leased"
            assert stored.result_json is None
    await harness.close()
