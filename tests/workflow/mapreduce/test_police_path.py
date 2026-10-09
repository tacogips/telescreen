"""Police partition and result-sink behavior for map sweeps."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.json import dump_json, json_object
from telescreen.core.vocabulary import (
    EngineTaskKind,
    ErrorCode,
    MapItemStatus,
    MapJobStatus,
    MapOrigin,
    MapPartitionStatus,
)
from telescreen.db.models import MapItemRow, MapJobRow, MapPartitionRow
from telescreen.masking.redact import Redactor
from telescreen.ports.police import PoliceTaskRequest
from telescreen.ports.sweeps import PartitionPage, ShardFailure, ShardSpec, ShardSummary
from telescreen.workflow.dsl.model import MapVia
from telescreen.workflow.mapreduce.dispatch import MapDispatchHandler
from telescreen.workflow.mapreduce.partition import MapPartitionHandler
from telescreen.workflow.mapreduce.reduce import MapReduceHandler
from telescreen.workflow.mapreduce.service import spec_to_json
from telescreen.workflow.mapreduce.sink import MapResultSinkImpl
from telescreen.workflow.mapreduce.spec import MapJobSpec
from tests.support.db import SqliteHarness, make_sqlite_harness
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
)


@dataclass
class SequencedGateway:
    """Return distinct Police task ids for dispatched shards."""

    counter: int = 0

    async def dispatch(self, session: AsyncSession, request: PoliceTaskRequest) -> str:
        del session, request
        self.counter += 1
        return f"police-task-{self.counter}"

    async def cancel(self, session: AsyncSession, task_id: str) -> None:
        del session, task_id


def _police_spec() -> MapJobSpec:
    """Build a Police-partitioned and Police-scanned sweep policy."""
    return MapJobSpec(
        MapVia.POLICE,
        MapVia.POLICE,
        "partition",
        "scan_shard",
        (),
        (),
        32,
        timedelta(minutes=15),
        3,
        0.01,
        100,
        timedelta(days=1),
    )


def _job(job_id: str, now: datetime) -> MapJobRow:
    """Create a Police map job at the partitioning stage."""
    spec: MapJobSpec = _police_spec()
    return MapJobRow(
        id=job_id,
        origin=MapOrigin.COLLECTOR.value,
        step_run_id=None,
        collector_config_id="collector-1",
        spec_json=dump_json(spec_to_json(spec)),
        status=MapJobStatus.PARTITIONING.value,
        partition_done=False,
        max_concurrency=32,
        paused_until=None,
        deadline=now + timedelta(days=1),
        result_json=None,
        created_at=now,
        finished_at=None,
    )


def _leased_partition(partition_id: str, job_id: str, police_task_id: str) -> MapPartitionRow:
    """Create an active Police partition lease."""
    return MapPartitionRow(
        id=partition_id,
        map_job_id=job_id,
        corpus_key="",
        cursor=None,
        status=MapPartitionStatus.LEASED.value,
        attempt=1,
        police_task_id=police_task_id,
    )


@pytest.mark.asyncio
async def test_police_partition_page_dispatch_and_partial_reduce() -> None:
    """Apply one Police page, ignore its replay, and reduce failed resources as partial."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        gateway: SequencedGateway = SequencedGateway()
        issues: IssueFake = IssueFake([])
        async with harness.sessions.begin() as session:
            session.add(_job("job", now))
            session.add(_leased_partition("partition", "job", "partition-task"))
        partitions: MapPartitionHandler = MapPartitionHandler(
            harness.sessions,
            SweeperFake(),
            gateway,
            ConfigFake(_config()),
            Redactor.default(),
            clock,
            queue,
        )
        sink: MapResultSinkImpl = MapResultSinkImpl(queue, clock, Redactor.default(), partitions)
        page: PartitionPage = PartitionPage(
            (
                ShardSpec("shard-a", json_object(()), 1),
                ShardSpec("shard-b", json_object(()), 1),
            ),
            None,
            (),
        )
        async with harness.sessions.begin() as session:
            await sink.partition_page(session, "partition-task", page)
        async with harness.sessions() as session:
            item_count: int = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MapItemRow)
                    .where(MapItemRow.map_job_id == "job")
                )
                or 0
            )
            items: list[MapItemRow] = list(
                await session.scalars(select(MapItemRow).where(MapItemRow.map_job_id == "job"))
            )
            partition: MapPartitionRow | None = await session.get(MapPartitionRow, "partition")
            job: MapJobRow | None = await session.get(MapJobRow, "job")
            assert item_count == 2
            assert all(item.status == MapItemStatus.READY.value for item in items)
            assert partition is not None
            assert partition.status == MapPartitionStatus.DONE.value
            assert partition.police_task_id is None
            assert job is not None and job.partition_done
            assert job.status == MapJobStatus.RUNNING.value
        assert f"mapdisp:job:{int(now.timestamp())}" in queue.keys
        async with harness.sessions.begin() as session:
            await sink.partition_page(session, "partition-task", page)
        async with harness.sessions() as session:
            replay_count: int = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MapItemRow)
                    .where(MapItemRow.map_job_id == "job")
                )
                or 0
            )
            assert replay_count == 2

        dispatcher: MapDispatchHandler = MapDispatchHandler(
            harness.sessions,
            queue,
            gateway,
            ScanJobsFake(),
            ConfigFake(_config()),
            clock,
            2,
        )
        assert await dispatcher._handle_id("job") == 2
        async with harness.sessions() as session:
            items = list(
                await session.scalars(select(MapItemRow).where(MapItemRow.map_job_id == "job"))
            )
            assert len(items) == 2
            assert all(item.status == MapItemStatus.DISPATCHED.value for item in items)
            task_ids: list[str] = [item.police_task_id or "" for item in items]
            assert len(set(task_ids)) == 2
            assert all(task_id for task_id in task_ids)
        for index, task_id in enumerate(task_ids):
            summary: ShardSummary = (
                ShardSummary(0, 0, 1, 0, 0, 0, 0, ()) if index == 0 else ShardSummary.empty()
            )
            async with harness.sessions.begin() as session:
                await sink.shard_finished(session, task_id, summary)
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            queue,
            gateway,
            issues,
            SignalFake(),
            AuditFake(),
            clock,
        )
        await reducer._handle_id("job")
        async with harness.sessions() as session:
            job = await session.get(MapJobRow, "job")
            assert job is not None and job.status == MapJobStatus.PARTIAL.value
        assert issues.calls == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_police_partition_failure_retries_twice_then_fails() -> None:
    """A Police partition retries through attempt three before preserving its error code."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        async with harness.sessions.begin() as session:
            session.add(_job("job", now))
            session.add(_leased_partition("partition", "job", "police-task-1"))
        sink: MapResultSinkImpl = MapResultSinkImpl(
            queue,
            clock,
            Redactor.default(),
            MapPartitionHandler(
                harness.sessions,
                SweeperFake(),
                GatewayFake(),
                ConfigFake(_config()),
                Redactor.default(),
                clock,
                queue,
            ),
        )
        for attempt in range(1, 4):
            async with harness.sessions.begin() as session:
                await sink.task_failed(
                    session,
                    f"police-task-{attempt}",
                    ShardFailure(ErrorCode.FETCH_FAILED),
                )
            async with harness.sessions() as session:
                partition: MapPartitionRow | None = await session.get(MapPartitionRow, "partition")
                job: MapJobRow | None = await session.get(MapJobRow, "job")
                assert partition is not None and job is not None
                if attempt < 3:
                    assert partition.status == MapPartitionStatus.READY.value
                    assert partition.attempt == attempt + 1
                    assert f"mappart:partition:{attempt + 1}:retry" in queue.keys
                else:
                    assert partition.status == MapPartitionStatus.FAILED.value
                    assert partition.cursor == ErrorCode.FETCH_FAILED.value
                    assert job.partition_done
            if attempt < 3:
                async with harness.sessions.begin() as session:
                    retry_partition: MapPartitionRow | None = await session.get(
                        MapPartitionRow, "partition"
                    )
                    assert retry_partition is not None
                    retry_partition.police_task_id = f"police-task-{attempt + 1}"
                    retry_partition.status = MapPartitionStatus.LEASED.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_police_partition_rate_limit_preserves_attempt_and_pauses() -> None:
    """A Police partition 429 retains its attempt and schedules its pause deadline."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        async with harness.sessions.begin() as session:
            session.add(_job("job", now))
            partition: MapPartitionRow = _leased_partition("partition", "job", "police-task")
            partition.attempt = 2
            session.add(partition)
        sink: MapResultSinkImpl = MapResultSinkImpl(
            queue,
            clock,
            Redactor.default(),
            MapPartitionHandler(
                harness.sessions,
                SweeperFake(),
                GatewayFake(),
                ConfigFake(_config()),
                Redactor.default(),
                clock,
                queue,
            ),
        )
        async with harness.sessions.begin() as session:
            await sink.task_failed(
                session,
                "police-task",
                ShardFailure(ErrorCode.RATE_LIMITED, 10),
            )
        async with harness.sessions() as session:
            partition: MapPartitionRow | None = await session.get(MapPartitionRow, "partition")
            job: MapJobRow | None = await session.get(MapJobRow, "job")
            assert partition is not None and partition.status == MapPartitionStatus.READY.value
            assert partition.attempt == 2
            assert job is not None and job.paused_until is not None
            assert job.paused_until.replace(tzinfo=UTC) == now + timedelta(seconds=10)
        assert any(
            spec.kind is EngineTaskKind.MAP_PARTITION
            and spec.idempotency_key.startswith("mappart:partition:2:rate-limit:")
            for spec in queue.specs
        )
    finally:
        await harness.close()
