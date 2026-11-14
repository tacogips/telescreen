"""Regression coverage for retries after durable task keys were consumed."""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.json import json_str
from telescreen.core.vocabulary import (
    EngineTaskKind,
    EngineTaskStatus,
    MapItemStatus,
    MapJobStatus,
    MapPartitionStatus,
)
from telescreen.db.models import EngineTaskRow, MapJobRow, MapPartitionRow
from telescreen.masking.redact import Redactor
from telescreen.ports.sweeps import PartitionPage
from telescreen.ports.workflow import StepSignal, TaskSpec
from telescreen.workflow.mapreduce.dispatch import MapDispatchHandler
from telescreen.workflow.mapreduce.partition import MapPartitionHandler
from telescreen.workflow.mapreduce.reduce import MapReduceHandler
from telescreen.workflow.mapreduce.shard import MapShardHandler
from telescreen.workflow.mapreduce.spec import MapJobSpec
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.workflow.mapreduce.test_mapreduce import (
    AuditFake,
    ConfigFake,
    GatewayFake,
    IssueFake,
    ScanJobsFake,
    SweeperFake,
    _config,
    _spec,
)
from tests.workflow.mapreduce.test_recovery import _map_item, _map_job


@dataclass
class SignalRecorder:
    """Record workflow completion signals from both reductions."""

    signals: list[StepSignal] = field(default_factory=list)

    async def signal(self, session: AsyncSession, step_run_id: str, signal: StepSignal) -> None:
        del session
        assert step_run_id == "step-run"
        self.signals.append(signal)


@pytest.mark.asyncio
async def test_retry_failed_job_reduces_again_with_real_sql_queue() -> None:
    """A completed reduce key cannot block a reopened job's finalization."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: SqlTaskQueue = SqlTaskQueue(clock)
        signals: SignalRecorder = SignalRecorder()
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            IssueFake([]),
            signals,
            AuditFake(),
            clock,
        )
        async with harness.sessions.begin() as session:
            job: MapJobRow = _map_job("retry-job", now)
            job.step_run_id = "step-run"
            session.add(job)
            session.add(
                _map_item(
                    "retry-job",
                    "retry-item",
                    now,
                    status=MapItemStatus.FAILED,
                    attempt=3,
                    error_code="fetch_failed",
                )
            )
            await queue.enqueue(
                session,
                TaskSpec(
                    EngineTaskKind.MAP_REDUCE,
                    job.id,
                    json_str(job.id),
                    now,
                    "mapreduce:retry-job",
                ),
            )
            old_reduce: EngineTaskRow | None = await session.scalar(
                select(EngineTaskRow).where(EngineTaskRow.idempotency_key == "mapreduce:retry-job")
            )
            assert old_reduce is not None
            old_reduce.status = EngineTaskStatus.DONE.value
        await reducer._handle_id("retry-job")
        assert len(signals.signals) == 1

        async with harness.sessions.begin() as session:
            await reducer.retry_failed(session, "retry-job")
            retry_dispatch: EngineTaskRow | None = await session.scalar(
                select(EngineTaskRow).where(
                    EngineTaskRow.kind == EngineTaskKind.MAP_DISPATCH.value,
                    EngineTaskRow.idempotency_key.like("mapdisp:retry-job:retry:%"),
                )
            )
            assert retry_dispatch is not None
        dispatcher: MapDispatchHandler = MapDispatchHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            ScanJobsFake(),
            ConfigFake(_config()),
            clock,
            tick_seconds=2,
        )
        await dispatcher._handle_id("retry-job", retry_dispatch.idempotency_key)
        scanner: MapShardHandler = MapShardHandler(harness.sessions, SweeperFake(), queue, clock)
        await scanner._handle_id("retry-item")
        await dispatcher._handle_id("retry-job", "mapdisp:retry-job:after-shard")

        async with harness.sessions() as session:
            reduce_keys: list[str] = list(
                await session.scalars(
                    select(EngineTaskRow.idempotency_key).where(
                        EngineTaskRow.kind == EngineTaskKind.MAP_REDUCE.value
                    )
                )
            )
            assert len(reduce_keys) == 2
            assert len(set(reduce_keys)) == 2
            retried_job: MapJobRow | None = await session.get(MapJobRow, "retry-job")
            assert retried_job is not None and retried_job.status == MapJobStatus.RUNNING.value
            assert retried_job.result_json is None
        await reducer._handle_id("retry-job")

        async with harness.sessions() as session:
            completed: MapJobRow | None = await session.get(MapJobRow, "retry-job")
            assert completed is not None
            assert completed.status == MapJobStatus.SUCCEEDED.value
            assert completed.result_json is not None
        assert len(signals.signals) == 2
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_reopened_partition_has_fresh_keys_for_continuation_and_second_retry() -> None:
    """A reset partition gets new IDs so old page/retry keys stay consumed safely."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: SqlTaskQueue = SqlTaskQueue(clock)
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            IssueFake([]),
            SignalRecorder(),
            AuditFake(),
            clock,
        )
        spec: MapJobSpec = _spec()
        cursor_hash: str = hashlib.sha256(b"same-cursor").hexdigest()[:16]
        async with harness.sessions.begin() as session:
            job: MapJobRow = _map_job("partition-job", now, spec)
            job.status = MapJobStatus.FAILED.value
            job.partition_done = False
            session.add(job)
            session.add(
                MapPartitionRow(
                    id="old-partition",
                    map_job_id=job.id,
                    corpus_key="failed-corpus",
                    cursor="fetch_failed",
                    status=MapPartitionStatus.FAILED.value,
                    attempt=3,
                    police_task_id=None,
                )
            )
            for key in (
                "mappart:old-partition:1:root",
                f"mappart:old-partition:1:{cursor_hash}",
                "mappart:old-partition:2:retry",
                "mappart:old-partition:1:retry",
            ):
                await queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.MAP_PARTITION,
                        "old-partition",
                        json_str("old-partition"),
                        now,
                        key,
                    ),
                )
        async with harness.sessions.begin() as session:
            await reducer.retry_failed(session, "partition-job")
        async with harness.sessions() as session:
            first: MapPartitionRow | None = await session.scalar(
                select(MapPartitionRow).where(MapPartitionRow.map_job_id == "partition-job")
            )
            assert first is not None and first.id != "old-partition"
            first_partition_id: str = first.id
            root_task: EngineTaskRow | None = await session.scalar(
                select(EngineTaskRow).where(
                    EngineTaskRow.idempotency_key == f"mappart:{first_partition_id}:1:root"
                )
            )
            assert root_task is not None

        partitioner: MapPartitionHandler = MapPartitionHandler(
            harness.sessions,
            SweeperFake(),
            GatewayFake(),
            ConfigFake(_config()),
            Redactor.default(),
            clock,
            queue,
        )
        same_cursor_page: PartitionPage = PartitionPage((), "same-cursor", ())
        async with harness.sessions.begin() as session:
            first = await session.get(MapPartitionRow, first_partition_id)
            assert first is not None
            first.attempt = 1
            await partitioner.apply_page(session, first, same_cursor_page)
            first.status = MapPartitionStatus.FAILED.value
            retry_job: MapJobRow | None = await session.get(MapJobRow, "partition-job")
            assert retry_job is not None
            retry_job.status = MapJobStatus.FAILED.value
        async with harness.sessions() as session:
            continuation: EngineTaskRow | None = await session.scalar(
                select(EngineTaskRow).where(
                    EngineTaskRow.kind == EngineTaskKind.MAP_PARTITION.value,
                    EngineTaskRow.idempotency_key.like(f"mappart:{first_partition_id}:1:%"),
                )
            )
            assert continuation is not None
            assert continuation.status == EngineTaskStatus.READY.value
            first_continuation_key: str = continuation.idempotency_key
        async with harness.sessions.begin() as session:
            await reducer.retry_failed(session, "partition-job")
        async with harness.sessions() as session:
            second: MapPartitionRow | None = await session.scalar(
                select(MapPartitionRow).where(MapPartitionRow.map_job_id == "partition-job")
            )
            assert second is not None and second.id != first_partition_id
            second_task: EngineTaskRow | None = await session.scalar(
                select(EngineTaskRow).where(
                    EngineTaskRow.idempotency_key == f"mappart:{second.id}:1:root"
                )
            )
            assert second_task is not None
            assert second_task.idempotency_key != first_continuation_key
    finally:
        await harness.close()
