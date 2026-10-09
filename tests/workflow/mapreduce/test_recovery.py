"""Retry, idempotency and Police result regression tests for map jobs."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json, json_object, json_str
from telescreen.core.vocabulary import (
    ErrorCode,
    MapItemStatus,
    MapJobStatus,
    MapOrigin,
    MapPartitionStatus,
    ScanTrigger,
)
from telescreen.db.models import MapItemRow, MapJobRow, MapPartitionRow
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import RateLimited
from telescreen.ports.police import PoliceTaskRequest
from telescreen.ports.sweeps import PartitionPage, PartitionRequest, ShardSpec, ShardSummary
from telescreen.ports.workflow import StepSignal, StepSignalKind
from telescreen.workflow.dsl.context import RunContext, RunInfo
from telescreen.workflow.dsl.model import MapStep, Outcomes3
from telescreen.workflow.executors.map import MapExecutor
from telescreen.workflow.mapreduce.dispatch import MapDispatchHandler
from telescreen.workflow.mapreduce.launcher import MapSweepLauncher
from telescreen.workflow.mapreduce.partition import MapPartitionHandler
from telescreen.workflow.mapreduce.reduce import MapReduceHandler
from telescreen.workflow.mapreduce.service import MapJobService, spec_to_json
from telescreen.workflow.mapreduce.shard import MapShardHandler
from telescreen.workflow.mapreduce.sink import MapResultSinkImpl
from telescreen.workflow.mapreduce.spec import MapJobSpec
from telescreen.workflow.step_api import StepContext, StepOutcome, StepOutcomeKind, WaitKind
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import github_token
from tests.workflow.mapreduce.test_mapreduce import (
    AuditFake,
    ConfigFake,
    GatewayFake,
    IssueFake,
    QueueFake,
    ScanJobsFake,
    SignalFake,
    SweeperFake,
    _config,
    _spec,
)


@dataclass
class RateLimitedSweeper(SweeperFake):
    """Raise one configured retry delay while scanning a shard."""

    async def scan_shard(
        self, collector_config_id: str, shard: ShardSpec, scan_job_id: str
    ) -> ShardSummary:
        del collector_config_id, shard, scan_job_id
        raise RateLimited(10)


@dataclass
class RateLimitedPartitioner(SweeperFake):
    """Raise a fixed partition retry delay."""

    async def partition(self, collector_config_id: str, request: PartitionRequest) -> PartitionPage:
        del collector_config_id, request
        raise RateLimited(10)


@dataclass
class FailingPartitioner(SweeperFake):
    """Raise a stable non-rate-limit partition error."""

    async def partition(self, collector_config_id: str, request: PartitionRequest) -> PartitionPage:
        del collector_config_id, request
        raise TelescreenError(ErrorCode.FETCH_FAILED, "partition failed")


@dataclass
class StaleResultSweeper(SweeperFake):
    """Advance the item attempt before returning an obsolete scan result."""

    sessions: async_sessionmaker[AsyncSession]
    item_id: str

    async def scan_shard(
        self, collector_config_id: str, shard: ShardSpec, scan_job_id: str
    ) -> ShardSummary:
        del collector_config_id, shard, scan_job_id
        async with self.sessions.begin() as session:
            item: MapItemRow | None = await session.get(MapItemRow, self.item_id)
            assert item is not None
            item.attempt += 1
        return ShardSummary.empty()


@dataclass
class TrackingGatewayFake:
    """Record Police cancellations made by reducer control operations."""

    cancelled: list[str]

    async def dispatch(self, session: AsyncSession, request: PoliceTaskRequest) -> str:
        del session, request
        return "police-task"

    async def cancel(self, session: AsyncSession, task_id: str) -> None:
        del session
        self.cancelled.append(task_id)


def _map_job(job_id: str, now: datetime, spec: MapJobSpec | None = None) -> MapJobRow:
    """Create a persisted-job fixture with collector defaults."""
    policy: MapJobSpec = spec or _spec()
    return MapJobRow(
        id=job_id,
        origin=MapOrigin.COLLECTOR.value,
        step_run_id=None,
        collector_config_id="collector-1",
        spec_json=dump_json(spec_to_json(policy)),
        status=MapJobStatus.RUNNING.value,
        partition_done=True,
        max_concurrency=policy.max_concurrency,
        paused_until=None,
        deadline=now + timedelta(days=1),
        result_json=None,
        created_at=now,
        finished_at=None,
    )


def _map_item(
    job_id: str,
    item_id: str,
    now: datetime,
    status: MapItemStatus,
    attempt: int = 0,
    error_code: str | None = None,
    police_task_id: str | None = None,
) -> MapItemRow:
    """Create an item fixture containing only an opaque descriptor."""
    return MapItemRow(
        id=item_id,
        map_job_id=job_id,
        shard_key=f"shard-{item_id}",
        descriptor_json='{"id":"opaque"}',
        weight=1.0,
        status=status.value,
        attempt=attempt,
        dispatched_at=now if status is MapItemStatus.DISPATCHED else None,
        finished_at=None,
        summary_json=None,
        error_code=error_code,
        police_task_id=police_task_id,
        scan_job_id="scan-1",
    )


@pytest.mark.asyncio
async def test_partition_page_replay_is_idempotent() -> None:
    """Reapplying the same page does not create duplicate shard rows."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        async with harness.sessions.begin() as session:
            session.add(_map_job("job", now))
            session.add(
                MapPartitionRow(
                    id="partition",
                    map_job_id="job",
                    corpus_key="",
                    cursor=None,
                    status=MapPartitionStatus.READY.value,
                    attempt=1,
                    police_task_id=None,
                )
            )
        handler: MapPartitionHandler = MapPartitionHandler(
            harness.sessions,
            SweeperFake(),
            GatewayFake(),
            ConfigFake(_config()),
            Redactor.default(),
            clock,
            QueueFake(set(), []),
        )
        page: PartitionPage = PartitionPage((ShardSpec("same", json_object(()), 1),), None, ())
        async with harness.sessions.begin() as session:
            partition: MapPartitionRow | None = await session.get(MapPartitionRow, "partition")
            assert partition is not None
            await handler.apply_page(session, partition, page)
        async with harness.sessions.begin() as session:
            partition = await session.get(MapPartitionRow, "partition")
            assert partition is not None
            await handler.apply_page(session, partition, page)
        async with harness.sessions() as session:
            count: int = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MapItemRow)
                    .where(MapItemRow.map_job_id == "job")
                )
                or 0
            )
            assert count == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_partition_retries_rate_limit_and_fails_stable_errors() -> None:
    """Partition 429 preserves attempts while repeated failures reach attempt three."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        async with harness.sessions.begin() as session:
            session.add(_map_job("rate-job", now))
            session.add(_map_job("error-job", now))
            for job_id, partition_id in (
                ("rate-job", "rate-partition"),
                ("error-job", "error-partition"),
            ):
                session.add(
                    MapPartitionRow(
                        id=partition_id,
                        map_job_id=job_id,
                        corpus_key="",
                        cursor=None,
                        status=MapPartitionStatus.READY.value,
                        attempt=1,
                        police_task_id=None,
                    )
                )
        rate_handler: MapPartitionHandler = MapPartitionHandler(
            harness.sessions,
            RateLimitedPartitioner(),
            GatewayFake(),
            ConfigFake(_config()),
            Redactor.default(),
            clock,
            queue,
        )
        await rate_handler._handle_id("rate-partition")
        async with harness.sessions() as session:
            rate_partition: MapPartitionRow | None = await session.get(
                MapPartitionRow, "rate-partition"
            )
            rate_job: MapJobRow | None = await session.get(MapJobRow, "rate-job")
            assert rate_partition is not None
            assert rate_partition.status == MapPartitionStatus.READY.value
            assert rate_partition.attempt == 1
            assert rate_job is not None and rate_job.paused_until is not None
            assert rate_job.paused_until.replace(tzinfo=UTC) == now + timedelta(seconds=10)
        error_handler: MapPartitionHandler = MapPartitionHandler(
            harness.sessions,
            FailingPartitioner(),
            GatewayFake(),
            ConfigFake(_config()),
            Redactor.default(),
            clock,
            queue,
        )
        for _ in range(3):
            await error_handler._handle_id("error-partition")
        async with harness.sessions() as session:
            error_partition: MapPartitionRow | None = await session.get(
                MapPartitionRow, "error-partition"
            )
            assert error_partition is not None
            assert error_partition.status == MapPartitionStatus.FAILED.value
            assert error_partition.cursor == ErrorCode.FETCH_FAILED.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_rate_limit_restores_attempt_and_pauses_dispatch() -> None:
    """A 429 returns a shard to ready without consuming its attempt."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        async with harness.sessions.begin() as session:
            session.add(_map_job("job", now))
            session.add(_map_item("job", "item", now, MapItemStatus.DISPATCHED, 1))
        shard_handler: MapShardHandler = MapShardHandler(
            harness.sessions, RateLimitedSweeper(), queue, clock
        )
        await shard_handler._handle_id("item")
        async with harness.sessions() as session:
            item: MapItemRow | None = await session.get(MapItemRow, "item")
            job: MapJobRow | None = await session.get(MapJobRow, "job")
            assert item is not None and item.status == MapItemStatus.READY.value
            assert item.attempt == 0
            assert job is not None and job.paused_until is not None
            assert job.paused_until.replace(tzinfo=UTC) == now + timedelta(seconds=10)
        dispatcher: MapDispatchHandler = MapDispatchHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            ScanJobsFake(),
            ConfigFake(_config()),
            clock,
            2,
        )
        assert await dispatcher._handle_id("job") == 0
        clock.advance(timedelta(seconds=10))
        assert await dispatcher._handle_id("job") == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_stale_shard_result_is_ignored() -> None:
    """An older attempt cannot overwrite the current attempt's item state."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        async with harness.sessions.begin() as session:
            session.add(_map_job("job", now))
            session.add(_map_item("job", "item", now, MapItemStatus.DISPATCHED, 1))
        handler: MapShardHandler = MapShardHandler(
            harness.sessions,
            StaleResultSweeper(harness.sessions, "item"),
            QueueFake(set(), []),
            clock,
        )
        await handler._handle_id("item")
        async with harness.sessions() as session:
            item: MapItemRow | None = await session.get(MapItemRow, "item")
            assert item is not None
            assert item.status == MapItemStatus.DISPATCHED.value
            assert item.attempt == 2
            assert item.summary_json is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_failure_bounds_reduce_to_partial_then_failed() -> None:
    """One in two hundred errors is partial; three exceed the 1% failure bound."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        issues: IssueFake = IssueFake([])
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            QueueFake(set(), []),
            GatewayFake(),
            issues,
            SignalFake(),
            AuditFake(),
            FakeClock(now),
        )
        async with harness.sessions.begin() as session:
            session.add(_map_job("one-failure", now))
            session.add(_map_job("three-failures", now))
            session.add_all(
                _map_item(
                    job_id,
                    f"{job_id}-{index}",
                    now,
                    MapItemStatus.FAILED if index < failures else MapItemStatus.SUCCEEDED,
                    error_code="fetch_failed" if index < failures else None,
                    attempt=3 if index < failures else 1,
                )
                for job_id, failures in (("one-failure", 1), ("three-failures", 3))
                for index in range(200)
            )
            for job_id in ("one-failure", "three-failures"):
                for index in range(200):
                    item: MapItemRow | None = await session.get(MapItemRow, f"{job_id}-{index}")
                    if item is not None and item.status == MapItemStatus.SUCCEEDED.value:
                        item.summary_json = '{"resources_failed":0,"resources_scanned":1}'
        await reducer._handle_id("one-failure")
        await reducer._handle_id("three-failures")
        async with harness.sessions() as session:
            partial: MapJobRow | None = await session.get(MapJobRow, "one-failure")
            failed: MapJobRow | None = await session.get(MapJobRow, "three-failures")
            assert partial is not None and partial.status == MapJobStatus.PARTIAL.value
            assert failed is not None and failed.status == MapJobStatus.FAILED.value
            assert failed.result_json is not None and "fetch_failed" in failed.result_json
        assert len(issues.calls) == 0
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_expired_shard_retries_then_fails_with_sweep_timeout() -> None:
    """Timed-out shards retry twice and fail with the stable timeout code."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        async with harness.sessions.begin() as session:
            session.add(_map_job("job", now))
            session.add(
                _map_item(
                    "job",
                    "item",
                    now - timedelta(minutes=20),
                    MapItemStatus.DISPATCHED,
                    1,
                )
            )
        handler: MapDispatchHandler = MapDispatchHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            ScanJobsFake(),
            ConfigFake(_config()),
            clock,
            2,
        )
        await handler._handle_id("job")
        async with harness.sessions() as session:
            item: MapItemRow | None = await session.get(MapItemRow, "item")
            assert item is not None
            assert item.status == MapItemStatus.DISPATCHED.value
            assert item.attempt == 2
        clock.advance(timedelta(minutes=20))
        await handler._handle_id("job")
        async with harness.sessions() as session:
            item = await session.get(MapItemRow, "item")
            assert item is not None
            assert item.status == MapItemStatus.DISPATCHED.value
            assert item.attempt == 3
        clock.advance(timedelta(minutes=20))
        await handler._handle_id("job")
        async with harness.sessions() as session:
            item = await session.get(MapItemRow, "item")
            assert item is not None
            assert item.status == MapItemStatus.FAILED.value
            assert item.error_code == ErrorCode.SWEEP_TIMEOUT.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_police_partial_summary_skips_reconciliation_and_late_result() -> None:
    """Police results preserve DR-1 and ignore callbacks for timed-out shards."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        issues: IssueFake = IssueFake([])
        async with harness.sessions.begin() as session:
            session.add(_map_job("police-job", now))
            session.add(_map_job("late-job", now))
            session.add(
                _map_item(
                    "police-job",
                    "police-item",
                    now,
                    MapItemStatus.DISPATCHED,
                    1,
                    police_task_id="police-task",
                )
            )
            session.add(
                _map_item(
                    "late-job",
                    "late-item",
                    now,
                    MapItemStatus.FAILED,
                    3,
                    error_code=ErrorCode.SWEEP_TIMEOUT.value,
                    police_task_id="late-task",
                )
            )
        partitioner: MapPartitionHandler = MapPartitionHandler(
            harness.sessions,
            SweeperFake(),
            GatewayFake(),
            ConfigFake(_config()),
            Redactor.default(),
            clock,
            queue,
        )
        sink: MapResultSinkImpl = MapResultSinkImpl(queue, clock, Redactor.default(), partitioner)
        summary: ShardSummary = ShardSummary(0, 0, 1, 0, 0, 0, 0, ())
        async with harness.sessions.begin() as session:
            await sink.shard_finished(session, "police-task", summary)
            await sink.shard_finished(session, "late-task", ShardSummary.empty())
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            issues,
            SignalFake(),
            AuditFake(),
            clock,
        )
        await reducer._handle_id("police-job")
        async with harness.sessions() as session:
            police_job: MapJobRow | None = await session.get(MapJobRow, "police-job")
            late_item: MapItemRow | None = await session.get(MapItemRow, "late-item")
            assert police_job is not None
            assert police_job.status == MapJobStatus.PARTIAL.value
            assert police_job.result_json is not None
            assert '"resources_failed":1' in police_job.result_json
            assert late_item is not None
            assert late_item.status == MapItemStatus.FAILED.value
        assert issues.calls == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_retry_failed_reopens_partition_and_shard_work() -> None:
    """Retry resets failed map items and failed partitions for another run."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        queue: QueueFake = QueueFake(set(), [])
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            IssueFake([]),
            SignalFake(),
            AuditFake(),
            FakeClock(now),
        )
        async with harness.sessions.begin() as session:
            job: MapJobRow = _map_job("job", now)
            job.status = MapJobStatus.FAILED.value
            job.partition_done = True
            session.add(job)
            session.add(_map_item("job", "item", now, MapItemStatus.FAILED, 3, "fetch_failed"))
            session.add(
                MapPartitionRow(
                    id="partition",
                    map_job_id="job",
                    corpus_key="failed-corpus",
                    cursor=ErrorCode.FETCH_FAILED.value,
                    status=MapPartitionStatus.FAILED.value,
                    attempt=3,
                    police_task_id=None,
                )
            )
            await reducer.retry_failed(session, "job")
        async with harness.sessions() as session:
            retried_job: MapJobRow | None = await session.get(MapJobRow, "job")
            item: MapItemRow | None = await session.get(MapItemRow, "item")
            partition: MapPartitionRow | None = await session.get(MapPartitionRow, "partition")
            assert retried_job is not None and retried_job.partition_done is False
            assert retried_job.status == MapJobStatus.RUNNING.value
            assert item is not None and item.status == MapItemStatus.READY.value
            assert partition is not None and partition.status == MapPartitionStatus.READY.value
            assert partition.cursor is None and partition.attempt == 1
        assert any(key.startswith("mappart:partition") for key in queue.keys)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_cancel_leaves_terminal_job_unchanged() -> None:
    """A repeated cancel does not mutate an already terminal job."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        queue: QueueFake = QueueFake(set(), [])
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            IssueFake([]),
            SignalFake(),
            AuditFake(),
            FakeClock(now),
        )
        async with harness.sessions.begin() as session:
            job: MapJobRow = _map_job("job", now)
            job.status = MapJobStatus.SUCCEEDED.value
            session.add(job)
            await reducer.cancel(session, "job")
        async with harness.sessions() as session:
            unchanged_job: MapJobRow | None = await session.get(MapJobRow, "job")
            assert unchanged_job is not None
            assert unchanged_job.status == MapJobStatus.SUCCEEDED.value
            assert unchanged_job.finished_at is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_cancel_stops_police_shards_and_partitions() -> None:
    """Cancel marks active work and cancels outstanding Police task leases."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        queue: QueueFake = QueueFake(set(), [])
        gateway: TrackingGatewayFake = TrackingGatewayFake([])
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            queue,
            gateway,
            IssueFake([]),
            SignalFake(),
            AuditFake(),
            FakeClock(now),
        )
        async with harness.sessions.begin() as session:
            session.add(_map_job("job", now))
            session.add(
                _map_item(
                    "job",
                    "item",
                    now,
                    MapItemStatus.DISPATCHED,
                    1,
                    police_task_id="shard-task",
                )
            )
            session.add(
                MapPartitionRow(
                    id="partition",
                    map_job_id="job",
                    corpus_key="leased-corpus",
                    cursor=None,
                    status=MapPartitionStatus.LEASED.value,
                    attempt=1,
                    police_task_id="partition-task",
                )
            )
            await reducer.cancel(session, "job")
        async with harness.sessions() as session:
            item: MapItemRow | None = await session.get(MapItemRow, "item")
            partition: MapPartitionRow | None = await session.get(MapPartitionRow, "partition")
            job: MapJobRow | None = await session.get(MapJobRow, "job")
            assert item is not None and item.status == MapItemStatus.CANCELLED.value
            assert partition is not None
            assert partition.status == MapPartitionStatus.FAILED.value
            assert partition.cursor == "cancelled"
            assert job is not None and job.status == MapJobStatus.CANCELLED.value
        assert gateway.cancelled == ["shard-task", "partition-task"]
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_launcher_rejects_second_active_collector_sweep() -> None:
    """Only one non-terminal collector sweep can exist for a configuration."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        service: MapJobService = MapJobService(QueueFake(set(), []), clock)
        launcher: MapSweepLauncher = MapSweepLauncher(
            service, harness.sessions, ConfigFake(_config()), clock
        )
        async with harness.sessions.begin() as session:
            session.add(_map_job("active", now))
        with pytest.raises(TelescreenError) as caught:
            await launcher.start_collector_sweep("collector-1", ScanTrigger.SWEEP)
        assert caught.value.code is ErrorCode.CONFLICT
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_raw_descriptor_fails_closed_and_map_executor_resumes() -> None:
    """Raw partition descriptors fail closed and map steps route their result."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        async with harness.sessions.begin() as session:
            session.add(_map_job("job", now))
            session.add(
                MapPartitionRow(
                    id="partition",
                    map_job_id="job",
                    corpus_key="",
                    cursor=None,
                    status=MapPartitionStatus.READY.value,
                    attempt=1,
                    police_task_id=None,
                )
            )
        partitioner: MapPartitionHandler = MapPartitionHandler(
            harness.sessions,
            SweeperFake(),
            GatewayFake(),
            ConfigFake(_config()),
            Redactor.default(),
            clock,
            queue,
        )
        async with harness.sessions.begin() as session:
            partition: MapPartitionRow | None = await session.get(MapPartitionRow, "partition")
            assert partition is not None
            await partitioner.apply_page(
                session,
                partition,
                PartitionPage(
                    (
                        ShardSpec(
                            "sensitive",
                            json_object((("key", json_str(github_token().value)),)),
                            1,
                        ),
                    ),
                    None,
                    (),
                ),
            )
        async with harness.sessions() as session:
            partition = await session.get(MapPartitionRow, "partition")
            assert partition is not None
            assert partition.status == MapPartitionStatus.FAILED.value
            assert partition.cursor == ErrorCode.RAW_VALUE_DETECTED.value

        step: MapStep = MapStep(
            id="map",
            collector="test",
            outcomes=Outcomes3(succeeded="done", partial="review", failed="alert"),
        )
        executor: MapExecutor = MapExecutor(MapJobService(queue, clock), ConfigFake(_config()))
        async with harness.sessions.begin() as session:
            context: StepContext = StepContext(
                session,
                "run",
                "step-run",
                step,
                1,
                1,
                RunContext(None, RunInfo("run", "flow", now.isoformat())),
                None,
                now,
            )
            waiting: StepOutcome = await executor.execute(context)
            assert waiting.kind is StepOutcomeKind.WAITING
            assert waiting.wait_kind is WaitKind.MAP
            resumed: StepOutcome = await executor.resume(
                context,
                StepSignal(
                    StepSignalKind.MAP_FINISHED,
                    "partial",
                    json_object((("map_job_id", json_str("job")),)),
                ),
            )
            assert resumed.kind is StepOutcomeKind.COMPLETED
            assert resumed.route == "review"
            assert resumed.output.get("map_job_id") is not None
        async with harness.sessions() as session:
            created_job_id: str | None = await session.scalar(
                select(MapJobRow.id).where(MapJobRow.step_run_id == "step-run")
            )
            assert created_job_id is not None
        assert executor.step_type.value == "map"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_whole_job_deadline_fails_even_without_shards() -> None:
    """An empty sweep past its deadline is failed rather than reconciled clean."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        issues: IssueFake = IssueFake([])
        async with harness.sessions.begin() as session:
            job: MapJobRow = _map_job("job", now)
            job.deadline = now - timedelta(seconds=1)
            session.add(job)
        dispatcher: MapDispatchHandler = MapDispatchHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            ScanJobsFake(),
            ConfigFake(_config()),
            clock,
            2,
        )
        await dispatcher._handle_id("job")
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            issues,
            SignalFake(),
            AuditFake(),
            clock,
        )
        await reducer._handle_id("job")
        async with harness.sessions() as session:
            timed_out_job: MapJobRow | None = await session.get(MapJobRow, "job")
            assert timed_out_job is not None
            assert timed_out_job.status == MapJobStatus.FAILED.value
            assert timed_out_job.result_json is not None
            assert '"failed_error_codes":[{"code":"sweep_timeout","count":1}]' in (
                timed_out_job.result_json
            )
        assert issues.calls == []
    finally:
        await harness.close()
