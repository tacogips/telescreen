"""Plan-required Police claim, result, maintenance and push regressions."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_null, json_object
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import (
    DetectorKind,
    EngineTaskKind,
    ErrorCode,
    PoliceTaskStatus,
)
from telescreen.db.models import PoliceTaskRow, PoliceWorkerRow
from telescreen.masking.redact import Redactor
from telescreen.police.claims import ClaimRequest, ClaimService
from telescreen.police.delivery import PolicePushDeliveryHandler
from telescreen.police.gateway import PullQueueTransport, RoutingPoliceGateway, WebhookTransport
from telescreen.police.maintenance import PoliceMaintenance
from telescreen.police.results import ResultService
from telescreen.ports.police import PoliceTaskRequest
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind
from tests.police.fakes import (
    FakeConfigs,
    FakeIssues,
    FakeMapSink,
    FakeQueue,
    FakeSignaler,
    RecordingAudit,
)
from tests.support.db import SqliteHarness, make_sqlite_harness


def _clock() -> FakeClock:
    """Return the fixed clock used by this test module."""
    return FakeClock(datetime(2026, 10, 9, tzinfo=UTC))


def _worker(
    clock: FakeClock,
    worker_id: str = "worker-id",
    mode: str = "pull",
    labels: str = "region=west",
    task_types: str = "verify_remediation,scan_shard,partition",
    endpoint_url: str | None = None,
    status: str = "active",
) -> PoliceWorkerRow:
    """Build a worker row for offline focused service tests."""
    return PoliceWorkerRow(
        id=worker_id,
        name=worker_id,
        version="1.0",
        mode=mode,
        contract_version="1.0",
        task_types=task_types,
        labels=labels,
        concurrency=2,
        endpoint_url=endpoint_url,
        token_hash=None,
        status=status,
        created_at=clock.now(),
    )


def _task(
    clock: FakeClock,
    task_id: str,
    task_type: str,
    status: str = "queued",
    worker_id: str | None = None,
    labels: str = "region=west",
    attempt: int = 0,
    max_attempts: int = 3,
    deadline: datetime | None = None,
    lease_expires_at: datetime | None = None,
    step_run_id: str | None = None,
) -> PoliceTaskRow:
    """Build a police task row with explicit lease and retry state."""
    return PoliceTaskRow(
        id=task_id,
        task_type=task_type,
        labels=labels,
        input_json="{}",
        status=status,
        worker_id=worker_id,
        attempt=attempt,
        max_attempts=max_attempts,
        lease_expires_at=lease_expires_at,
        deadline=deadline or clock.now() + timedelta(hours=1),
        step_run_id=step_run_id,
        created_at=clock.now(),
    )


def _results(
    clock: FakeClock, sink: FakeMapSink, issues: FakeIssues, signaler: FakeSignaler
) -> ResultService:
    """Build a result service with recording ports."""
    return ResultService(
        issues,
        FakeConfigs(),
        signaler,
        sink,
        Redactor.default(),
        RecordingAudit(),
        clock,
    )


@pytest.mark.asyncio
async def test_claim_label_mismatch_waits_until_long_poll_deadline() -> None:
    """A task outside the worker's label set is never leased during long poll."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    worker: PoliceWorkerRow = _worker(clock)
    task: PoliceTaskRow = _task(clock, "east-task", "verify_remediation", labels="region=east")
    async with harness.sessions.begin() as session:
        session.add_all((worker, task))
    sleep_count: list[int] = []

    async def counting_sleep(seconds: float) -> None:
        sleep_count.append(1)
        clock.advance(timedelta(seconds=seconds))

    claims: ClaimService = ClaimService(clock, counting_sleep)
    claimed: tuple[PoliceTaskRow, ...] = await claims.claim(
        harness.sessions, worker, ClaimRequest(1, 3)
    )
    assert claimed == ()
    assert len(sleep_count) == 3
    async with harness.sessions() as session:
        stored: PoliceTaskRow | None = await session.get(PoliceTaskRow, task.id)
        assert stored is not None and stored.status == PoliceTaskStatus.QUEUED.value
        assert stored.attempt == 0
    await harness.close()


@pytest.mark.asyncio
async def test_claim_skips_older_nonmatching_task_for_newer_matching_task() -> None:
    """A non-matching backlog cannot starve a worker of matching work."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    worker: PoliceWorkerRow = _worker(clock)
    east_task: PoliceTaskRow = _task(clock, "east-old", "verify_remediation", labels="region=east")
    clock.advance(timedelta(seconds=1))
    west_task: PoliceTaskRow = _task(clock, "west-new", "verify_remediation", labels="region=west")
    async with harness.sessions.begin() as session:
        session.add_all((worker, east_task, west_task))

    claimed: tuple[PoliceTaskRow, ...] = await ClaimService(clock).claim(
        harness.sessions, worker, ClaimRequest(1, 0)
    )

    assert tuple(task.id for task in claimed) == (west_task.id,)
    assert claimed[0].status == PoliceTaskStatus.LEASED.value
    assert claimed[0].worker_id == worker.id
    assert claimed[0].attempt == 1
    async with harness.sessions() as session:
        east_stored: PoliceTaskRow | None = await session.get(PoliceTaskRow, east_task.id)
        west_stored: PoliceTaskRow | None = await session.get(PoliceTaskRow, west_task.id)
        assert east_stored is not None
        assert east_stored.status == PoliceTaskStatus.QUEUED.value
        assert east_stored.worker_id is None and east_stored.attempt == 0
        assert west_stored is not None
        assert west_stored.status == PoliceTaskStatus.LEASED.value
        assert west_stored.worker_id == worker.id and west_stored.attempt == 1
    await harness.close()


@pytest.mark.asyncio
async def test_heartbeat_extends_lease_and_returns_cancelled_task_ids() -> None:
    """Heartbeat renews owned leases and identifies cancelled work."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    worker: PoliceWorkerRow = _worker(clock)
    leased: PoliceTaskRow = _task(
        clock,
        "leased-task",
        "verify_remediation",
        status="leased",
        worker_id=worker.id,
        attempt=1,
        lease_expires_at=clock.now() + timedelta(seconds=5),
    )
    cancelled: PoliceTaskRow = _task(
        clock, "cancelled-task", "verify_remediation", status="cancelled", worker_id=worker.id
    )
    async with harness.sessions.begin() as session:
        session.add_all((worker, leased, cancelled))
    clock.advance(timedelta(seconds=10))
    async with harness.sessions.begin() as session:
        current_worker: PoliceWorkerRow | None = await session.get(PoliceWorkerRow, worker.id)
        assert current_worker is not None
        cancelled_ids: tuple[str, ...] = await ClaimService(clock).heartbeat(
            session, current_worker, (leased.id, cancelled.id)
        )
        assert cancelled_ids == (cancelled.id,)
    async with harness.sessions() as session:
        stored: PoliceTaskRow | None = await session.get(PoliceTaskRow, leased.id)
        assert stored is not None
        assert stored.lease_expires_at is not None
        assert ensure_utc(stored.lease_expires_at) == clock.now() + timedelta(seconds=60)
    await harness.close()


@pytest.mark.asyncio
async def test_maintenance_routes_expired_map_tasks_to_sink() -> None:
    """Lease exhaustion and task deadlines both complete the map failure path."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    sink: FakeMapSink = FakeMapSink()
    lease_expired: PoliceTaskRow = _task(
        clock,
        "lease-expired",
        "scan_shard",
        status="leased",
        worker_id="worker-id",
        attempt=2,
        max_attempts=2,
        lease_expires_at=clock.now() - timedelta(seconds=1),
    )
    deadline_expired: PoliceTaskRow = _task(
        clock,
        "deadline-expired",
        "partition",
        status="delivered",
        worker_id="worker-id",
        deadline=clock.now() - timedelta(seconds=1),
    )
    async with harness.sessions.begin() as session:
        session.add_all((lease_expired, deadline_expired))
    async with harness.sessions.begin() as session:
        changed: int = await PoliceMaintenance(sink, clock).tick(session, clock.now())
    assert changed == 2
    assert {item[0] for item in sink.failures} == {lease_expired.id, deadline_expired.id}
    assert all(item[1].error_code is ErrorCode.FETCH_FAILED for item in sink.failures)
    async with harness.sessions() as session:
        stored_lease: PoliceTaskRow | None = await session.get(PoliceTaskRow, lease_expired.id)
        stored_deadline: PoliceTaskRow | None = await session.get(
            PoliceTaskRow, deadline_expired.id
        )
        assert stored_lease is not None and stored_lease.status == PoliceTaskStatus.FAILED.value
        assert (
            stored_deadline is not None and stored_deadline.status == PoliceTaskStatus.EXPIRED.value
        )
    await harness.close()


@pytest.mark.asyncio
async def test_verify_result_signals_pass_outcome() -> None:
    """A passed verification result signals its waiting workflow step."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    sink: FakeMapSink = FakeMapSink()
    issues: FakeIssues = FakeIssues()
    signaler: FakeSignaler = FakeSignaler()
    task: PoliceTaskRow = _task(
        clock, "verify-task", "verify_remediation", status="leased", step_run_id="step-run"
    )
    async with harness.sessions.begin() as session:
        session.add(task)
    body: bytes = (
        b'{"contract_version":"1.0","task_id":"verify-task",'
        b'"status":"completed","outcome":"passed","summary":"verified"}'
    )
    async with harness.sessions.begin() as session:
        current: PoliceTaskRow | None = await session.get(PoliceTaskRow, task.id)
        assert current is not None
        await _results(clock, sink, issues, signaler).accept(session, current, body)
    assert len(signaler.signals) == 1
    assert signaler.signals[0][0] == "step-run"
    assert signaler.signals[0][1].outcome == "passed"
    await harness.close()


@pytest.mark.asyncio
async def test_scan_shard_routes_counts_and_complete_locators() -> None:
    """Scan results reach issues and the count-only reducer with safe locators."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    sink: FakeMapSink = FakeMapSink()
    issues: FakeIssues = FakeIssues()
    task: PoliceTaskRow = _task(clock, "scan-task", "scan_shard", status="leased")
    async with harness.sessions.begin() as session:
        session.add(task)
    body: bytes = (
        b'{"contract_version":"1.0","task_id":"scan-task","status":"completed",'
        b'"stats":{"resources_scanned":4,"resources_failed":0,"bytes_scanned":80},'
        b'"complete_locators":["file-a","file-b"]}'
    )
    async with harness.sessions.begin() as session:
        current: PoliceTaskRow | None = await session.get(PoliceTaskRow, task.id)
        assert current is not None
        await _results(clock, sink, issues, FakeSignaler()).accept(session, current, body)
    assert len(issues.scans) == 1
    assert issues.scans[0].detector is DetectorKind.POLICE
    assert issues.scans[0].complete_locators == ("file-a", "file-b")
    assert len(sink.summaries) == 1
    assert sink.summaries[0][0] == task.id
    assert sink.summaries[0][1].resources_scanned == 4
    assert sink.summaries[0][1].resources_failed == 0
    await harness.close()


@pytest.mark.asyncio
async def test_scan_shard_with_failures_does_not_reconcile_locators() -> None:
    """Failed resource scans suppress complete locators before reconciliation."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    sink: FakeMapSink = FakeMapSink()
    issues: FakeIssues = FakeIssues()
    task: PoliceTaskRow = _task(clock, "scan-partial", "scan_shard", status="leased")
    async with harness.sessions.begin() as session:
        session.add(task)
    body: bytes = (
        b'{"contract_version":"1.0","task_id":"scan-partial","status":"completed",'
        b'"stats":{"resources_scanned":4,"resources_failed":1,"bytes_scanned":80},'
        b'"complete_locators":["file-a"]}'
    )
    async with harness.sessions.begin() as session:
        current: PoliceTaskRow | None = await session.get(PoliceTaskRow, task.id)
        assert current is not None
        await _results(clock, sink, issues, FakeSignaler()).accept(session, current, body)
    assert len(issues.scans) == 1 and issues.scans[0].complete_locators == ()
    assert len(sink.summaries) == 1 and sink.summaries[0][1].resources_failed == 1
    await harness.close()


@pytest.mark.asyncio
async def test_partition_rejects_more_than_one_thousand_shards() -> None:
    """A partition page larger than the contract limit is rejected before sink write."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    sink: FakeMapSink = FakeMapSink()
    task: PoliceTaskRow = _task(clock, "partition-task", "partition", status="leased")
    async with harness.sessions.begin() as session:
        session.add(task)
    shard_items: str = ",".join(
        '{"shard_key":"shard-' + str(index) + '","descriptor":{},"weight":1}'
        for index in range(1001)
    )
    body: bytes = (
        '{"contract_version":"1.0","task_id":"partition-task","status":"completed",'
        '"payload":{"shards":[' + shard_items + '],"next_cursor":null,"sub_corpora":[]}}'
    ).encode()
    with pytest.raises(TelescreenError) as error:
        async with harness.sessions.begin() as session:
            current: PoliceTaskRow | None = await session.get(PoliceTaskRow, task.id)
            assert current is not None
            await _results(clock, sink, FakeIssues(), FakeSignaler()).accept(session, current, body)
    assert error.value.code is ErrorCode.VALIDATION_ERROR
    assert sink.pages == []
    await harness.close()


@pytest.mark.asyncio
async def test_push_dispatch_enqueues_and_202_marks_task_delivered() -> None:
    """Matching push capability enqueues delivery and HTTP 202 records delivery."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    worker: PoliceWorkerRow = _worker(
        clock,
        worker_id="push-worker",
        mode="push",
        labels="region=west,team=security",
        endpoint_url="https://police.invalid/hook",
    )
    async with harness.sessions.begin() as session:
        session.add(worker)
    queue: FakeQueue = FakeQueue()
    gateway: RoutingPoliceGateway = RoutingPoliceGateway(
        PullQueueTransport(), WebhookTransport(queue, clock), clock
    )
    request: PoliceTaskRequest = PoliceTaskRequest(
        "verify_remediation",
        ("region=west",),
        json_object(()),
        clock.now() + timedelta(hours=1),
        max_attempts=3,
    )
    async with harness.sessions.begin() as session:
        task_id: str = await gateway.dispatch(session, request)
    assert len(queue.specs) == 1
    async with harness.sessions() as session:
        dispatched: PoliceTaskRow | None = await session.get(PoliceTaskRow, task_id)
        assert dispatched is not None
        assert dispatched.worker_id == worker.id and dispatched.attempt == 0

    async def accepted_post(request: httpx.Request) -> httpx.Response:
        """Return the asynchronous accepted status without network access."""
        del request
        return httpx.Response(202)

    client: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(accepted_post))
    sink: FakeMapSink = FakeMapSink()
    handler: PolicePushDeliveryHandler = PolicePushDeliveryHandler(
        harness.sessions,
        client,
        SecretValue("signing-placeholder"),
        "https://bureau.invalid",
        _results(clock, sink, FakeIssues(), FakeSignaler()),
        clock,
    )
    delivery_task: ClaimedTask = ClaimedTask(
        "engine-id",
        EngineTaskKind.POLICE_PUSH_DELIVERY,
        task_id,
        json_null(),
        1,
        5,
        "police-push:" + task_id + ":1",
    )
    disposition: TaskResult = await handler.handle(delivery_task)
    assert disposition.kind is TaskResultKind.DONE
    async with harness.sessions() as session:
        delivered: PoliceTaskRow | None = await session.get(PoliceTaskRow, task_id)
        updated_worker: PoliceWorkerRow | None = await session.get(PoliceWorkerRow, worker.id)
        assert delivered is not None and delivered.status == PoliceTaskStatus.DELIVERED.value
        assert delivered.attempt == 1 and delivered.callback_token_hash is not None
        assert updated_worker is not None and updated_worker.last_delivery_at is not None
        assert ensure_utc(updated_worker.last_delivery_at) == clock.now()
    await client.aclose()
    await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ("verify_remediation", "scan_shard"))
async def test_push_retry_exhaustion_routes_only_map_tasks(task_type: str) -> None:
    """Retries keep their timing; only exhausted map work reaches the reducer."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    worker: PoliceWorkerRow = _worker(
        clock,
        worker_id="push-worker",
        mode="push",
        endpoint_url="https://police.invalid/hook",
    )
    task: PoliceTaskRow = _task(
        clock,
        "retry-task-" + task_type,
        task_type,
        worker_id=worker.id,
        max_attempts=3,
    )
    async with harness.sessions.begin() as session:
        session.add_all((worker, task))

    async def failed_post(request: httpx.Request) -> httpx.Response:
        """Simulate a deterministic HTTP 500 for every push attempt."""
        del request
        return httpx.Response(500)

    client: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(failed_post))
    sink: FakeMapSink = FakeMapSink()
    handler: PolicePushDeliveryHandler = PolicePushDeliveryHandler(
        harness.sessions,
        client,
        SecretValue("signing-placeholder"),
        "https://bureau.invalid",
        _results(clock, sink, FakeIssues(), FakeSignaler()),
        clock,
    )
    engine_task: ClaimedTask = ClaimedTask(
        "engine-id",
        EngineTaskKind.POLICE_PUSH_DELIVERY,
        task.id,
        json_null(),
        1,
        5,
        "police-push:" + task.id + ":1",
    )
    first: TaskResult = await handler.handle(engine_task)
    second: TaskResult = await handler.handle(engine_task)
    third: TaskResult = await handler.handle(engine_task)
    assert first.kind is TaskResultKind.RETRY
    assert first.available_at == clock.now() + timedelta(seconds=10)
    assert second.kind is TaskResultKind.RETRY
    assert second.available_at == clock.now() + timedelta(seconds=20)
    assert third.kind is TaskResultKind.DEAD
    async with harness.sessions() as session:
        stored: PoliceTaskRow | None = await session.get(PoliceTaskRow, task.id)
        assert stored is not None
        assert stored.attempt == 3 and stored.status == PoliceTaskStatus.FAILED.value
    if task_type == "scan_shard":
        assert len(sink.failures) == 1
        assert sink.failures[0][0] == task.id
        assert sink.failures[0][1].error_code is ErrorCode.FETCH_FAILED
        assert sink.failures[0][1].retry_after_seconds is None
    else:
        assert sink.failures == []
    await client.aclose()
    await harness.close()


@pytest.mark.asyncio
async def test_missing_push_worker_fails_map_task_once() -> None:
    """A push map task with no worker reports terminal failure to the sink."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = _clock()
    task: PoliceTaskRow = _task(clock, "missing-worker", "partition", worker_id="deleted-worker")
    async with harness.sessions.begin() as session:
        session.add(task)

    async def no_post(request: httpx.Request) -> httpx.Response:
        """Fail if the missing-worker path unexpectedly performs network I/O."""
        del request
        return httpx.Response(500)

    client: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(no_post))
    sink: FakeMapSink = FakeMapSink()
    handler: PolicePushDeliveryHandler = PolicePushDeliveryHandler(
        harness.sessions,
        client,
        SecretValue("signing-placeholder"),
        "https://bureau.invalid",
        _results(clock, sink, FakeIssues(), FakeSignaler()),
        clock,
    )
    engine_task: ClaimedTask = ClaimedTask(
        "engine-id",
        EngineTaskKind.POLICE_PUSH_DELIVERY,
        task.id,
        json_null(),
        1,
        5,
        "police-push:" + task.id + ":1",
    )
    disposition: TaskResult = await handler.handle(engine_task)
    assert disposition.kind is TaskResultKind.DEAD
    assert len(sink.failures) == 1 and sink.failures[0][0] == task.id
    assert sink.failures[0][1].error_code is ErrorCode.FETCH_FAILED
    await client.aclose()
    await harness.close()
