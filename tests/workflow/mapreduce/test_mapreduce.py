"""Behavioral tests for durable map job dispatch and reduction."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json, json_null, json_object, json_str
from telescreen.core.vocabulary import (
    CollectorKind,
    EngineTaskKind,
    EngineTaskStatus,
    ErrorCode,
    MapItemStatus,
    MapJobStatus,
    MapOrigin,
    ScanJobStatus,
    ScanTrigger,
    TriggerMode,
)
from telescreen.db.models import EngineTaskRow, MapItemRow, MapJobRow, MapPartitionRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import CollectorConfig, CustomSelector
from telescreen.ports.findings import ExternalScan, RecordOutcome, ResourceScan
from telescreen.ports.issues import IssueHistoryEntry
from telescreen.ports.police import PoliceTaskRequest
from telescreen.ports.sweeps import (
    PartitionPage,
    PartitionRequest,
    ShardSpec,
    ShardSummary,
)
from telescreen.ports.workflow import (
    ClaimedTask,
    StepSignal,
    TaskHandler,
    TaskResult,
    TaskResultKind,
    TaskSpec,
)
from telescreen.workflow.dsl.model import MapVia
from telescreen.workflow.mapreduce.dispatch import MapDispatchHandler
from telescreen.workflow.mapreduce.partition import MapPartitionHandler
from telescreen.workflow.mapreduce.reduce import MapReduceHandler
from telescreen.workflow.mapreduce.service import spec_to_json
from telescreen.workflow.mapreduce.shard import MapShardHandler
from telescreen.workflow.mapreduce.spec import MapJobSpec
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class QueueFake:
    """Remember idempotent tasks for behavior assertions."""

    keys: set[str]
    attempts: list[str]
    specs: list[TaskSpec] = field(default_factory=list)

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        self.specs.append(spec)
        self.attempts.append(spec.idempotency_key)
        before: int = len(self.keys)
        self.keys.add(spec.idempotency_key)
        if spec.kind is EngineTaskKind.MAP_DISPATCH:
            existing: str | None = await session.scalar(
                select(EngineTaskRow.id).where(
                    EngineTaskRow.idempotency_key == spec.idempotency_key
                )
            )
            if existing is None:
                session.add(
                    EngineTaskRow(
                        id=new_id(),
                        kind=spec.kind.value,
                        ref_id=spec.ref_id,
                        payload_json=dump_json(spec.payload),
                        status=EngineTaskStatus.READY.value,
                        available_at=spec.available_at,
                        lease_owner=None,
                        lease_expires_at=None,
                        attempts=0,
                        max_attempts=spec.max_attempts,
                        idempotency_key=spec.idempotency_key,
                        last_error_code=None,
                        created_at=spec.available_at,
                    )
                )
        return len(self.keys) != before

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        del session, ref_id
        return 0


@dataclass
class ConfigFake:
    """Return a fixed collector config."""

    config: CollectorConfig

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        del session
        return self.config if collector_config_id == self.config.id else None

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        del session
        return self.config if name == self.config.name else None


@dataclass
class ScanJobsFake:
    """Return stable synthetic scan ids without external services."""

    counter: int = 0

    async def create(
        self,
        session: AsyncSession,
        collector_config_id: str | None,
        source: str,
        trigger: ScanTrigger,
        started_at: datetime,
    ) -> str:
        del session, collector_config_id, source, trigger, started_at
        self.counter += 1
        return f"scan-{self.counter}"

    async def finish(
        self,
        session: AsyncSession,
        scan_job_id: str,
        status: ScanJobStatus,
        resources_scanned: int,
        resources_skipped: int,
        findings_count: int,
        error_code: ErrorCode | None,
        finished_at: datetime,
    ) -> None:
        del session, scan_job_id, status, resources_scanned, resources_skipped
        del findings_count, error_code, finished_at


@dataclass
class GatewayFake:
    """Police gateway fake for collector-only tests."""

    async def dispatch(self, session: AsyncSession, request: PoliceTaskRequest) -> str:
        del session, request
        return "police-task"

    async def cancel(self, session: AsyncSession, task_id: str) -> None:
        del session, task_id


@dataclass
class SweeperFake:
    """Return empty count-only shard results without external services."""

    async def partition(self, collector_config_id: str, request: PartitionRequest) -> PartitionPage:
        del collector_config_id, request
        return PartitionPage((), None, ())

    async def scan_shard(
        self, collector_config_id: str, shard: ShardSpec, scan_job_id: str
    ) -> ShardSummary:
        del collector_config_id, shard, scan_job_id
        return ShardSummary.empty()


@dataclass
class TenPageSweeper(SweeperFake):
    """Produce ten pages of one thousand opaque shards each."""

    async def partition(self, collector_config_id: str, request: PartitionRequest) -> PartitionPage:
        del collector_config_id
        page: int = int(request.cursor or "0")
        shards: tuple[ShardSpec, ...] = tuple(
            ShardSpec(f"shard-{page * 1000 + index}", json_str("opaque"), 1)
            for index in range(1000)
        )
        next_cursor: str | None = str(page + 1) if page < 9 else None
        return PartitionPage(shards, next_cursor, ())


@dataclass
class IssueFake:
    """Track removal reconciliation calls."""

    calls: list[datetime]

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        del session, collector_config_id
        self.calls.append(checked_before)
        return 2

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        del session, entry

    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        del session, scan
        return RecordOutcome.empty()

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        del session, scan
        return RecordOutcome.empty()


@dataclass
class SignalFake:
    """Accept workflow signals in tests."""

    async def signal(self, session: AsyncSession, step_run_id: str, signal: StepSignal) -> None:
        del session, step_run_id, signal


@dataclass
class AuditFake:
    """Accept audit records in tests."""

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        del session, entry
        return 1


def _config() -> CollectorConfig:
    """Create a minimal sweep-capable collector config."""
    return CollectorConfig(
        "collector-1",
        "test",
        CollectorKind.GITHUB,
        TriggerMode.BOTH,
        None,
        None,
        32,
        CustomSelector(json_object(())),
        None,
        True,
    )


def _spec() -> MapJobSpec:
    """Return the default collector map limits for tests."""
    return MapJobSpec(
        MapVia.COLLECTOR,
        MapVia.COLLECTOR,
        None,
        None,
        (),
        (),
        32,
        timedelta(minutes=15),
        3,
        0.01,
        100,
        timedelta(days=1),
    )


@pytest.mark.asyncio
async def test_dispatch_caps_ten_thousand_shards_at_thirty_two() -> None:
    """One dispatch fills all slots; repeated same-tick dispatch cannot exceed cap."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        started: float = monotonic()
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        spec: MapJobSpec = _spec()
        async with harness.sessions.begin() as session:
            session.add(
                MapJobRow(
                    id="job",
                    origin=MapOrigin.COLLECTOR.value,
                    step_run_id=None,
                    collector_config_id="collector-1",
                    spec_json=dump_json(spec_to_json(spec)),
                    status=MapJobStatus.RUNNING.value,
                    partition_done=False,
                    max_concurrency=32,
                    paused_until=None,
                    deadline=now + timedelta(days=1),
                    result_json=None,
                    created_at=now,
                    finished_at=None,
                )
            )
            session.add(
                MapPartitionRow(
                    id="partition",
                    map_job_id="job",
                    corpus_key="",
                    cursor=None,
                    status="ready",
                    attempt=1,
                    police_task_id=None,
                )
            )
        partitioner: MapPartitionHandler = MapPartitionHandler(
            harness.sessions,
            TenPageSweeper(),
            GatewayFake(),
            ConfigFake(_config()),
            Redactor.default(),
            clock,
            queue,
        )
        await partitioner._handle_id("partition")
        async with harness.sessions() as session:
            ready_count: int = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MapItemRow)
                    .where(
                        MapItemRow.map_job_id == "job",
                        MapItemRow.status == MapItemStatus.READY.value,
                    )
                )
                or 0
            )
            assert ready_count == 1000
        handler: MapDispatchHandler = MapDispatchHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            ScanJobsFake(),
            ConfigFake(_config()),
            clock,
            2,
        )
        assert await handler._handle_id("job") == 32
        first_shard_specs: list[TaskSpec] = [
            task for task in queue.specs if task.kind is EngineTaskKind.MAP_SHARD
        ]
        assert len(first_shard_specs) == 32
        async with harness.sessions() as session:
            dispatched_items: list[MapItemRow] = list(
                await session.scalars(
                    select(MapItemRow).where(
                        MapItemRow.map_job_id == "job",
                        MapItemRow.status == MapItemStatus.DISPATCHED.value,
                    )
                )
            )
            assert {task.ref_id for task in first_shard_specs} == {
                item.id for item in dispatched_items
            }
            for task in first_shard_specs:
                item: MapItemRow = next(
                    candidate for candidate in dispatched_items if candidate.id == task.ref_id
                )
                assert task.idempotency_key == f"map:job:{item.shard_key}:{item.attempt}"
            peak: int = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MapItemRow)
                    .where(
                        MapItemRow.map_job_id == "job",
                        MapItemRow.status == MapItemStatus.DISPATCHED.value,
                    )
                )
                or 0
            )
            assert peak == 32
        assert await handler._handle_id("job") == 0
        async with harness.sessions() as session:
            peak = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MapItemRow)
                    .where(
                        MapItemRow.map_job_id == "job",
                        MapItemRow.status == MapItemStatus.DISPATCHED.value,
                    )
                )
                or 0
            )
            assert peak == 32
        same_tick_key: str = f"mapdisp:job:{int(now.timestamp()) // 2 + 1}"
        assert same_tick_key in queue.keys
        assert queue.attempts.count(same_tick_key) == 2
        async with harness.sessions() as session:
            dispatch_task_count: int = int(
                await session.scalar(
                    select(func.count())
                    .select_from(EngineTaskRow)
                    .where(EngineTaskRow.idempotency_key == same_tick_key)
                )
                or 0
            )
            assert dispatch_task_count == 1
        for _ in range(9):
            await partitioner._handle_id("partition")
        async with harness.sessions() as session:
            total_count: int = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MapItemRow)
                    .where(MapItemRow.map_job_id == "job")
                )
                or 0
            )
            assert total_count == 10000
        shard_handler: MapShardHandler = MapShardHandler(
            harness.sessions,
            SweeperFake(),
            queue,
            clock,
        )
        succeeded: int = 0
        consumed_specs: set[str] = set()
        iterations: int = 0
        while succeeded < 10000 and iterations < 1000:
            iterations += 1
            queued_shards: list[TaskSpec] = [
                task
                for task in queue.specs
                if task.kind is EngineTaskKind.MAP_SHARD
                and task.idempotency_key not in consumed_specs
            ]
            for task in queued_shards:
                result: TaskResult = await shard_handler.handle(
                    ClaimedTask(
                        new_id(),
                        EngineTaskKind.MAP_SHARD,
                        task.ref_id,
                        task.payload,
                        1,
                        task.max_attempts,
                        task.idempotency_key,
                    )
                )
                assert result.kind is TaskResultKind.DONE
                consumed_specs.add(task.idempotency_key)
            async with harness.sessions() as session:
                succeeded = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(MapItemRow)
                        .where(
                            MapItemRow.map_job_id == "job",
                            MapItemRow.status == MapItemStatus.SUCCEEDED.value,
                        )
                    )
                    or 0
                )
            if succeeded < 10000:
                clock.advance(timedelta(seconds=2))
                await handler._handle_id("job")
                async with harness.sessions() as session:
                    in_flight: int = int(
                        await session.scalar(
                            select(func.count())
                            .select_from(MapItemRow)
                            .where(
                                MapItemRow.map_job_id == "job",
                                MapItemRow.status == MapItemStatus.DISPATCHED.value,
                            )
                        )
                        or 0
                    )
                    assert in_flight <= 32
        assert iterations < 1000
        assert len(consumed_specs) == 10000
        await handler._handle_id("job")
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            queue,
            GatewayFake(),
            IssueFake([]),
            SignalFake(),
            AuditFake(),
            clock,
        )
        await reducer._handle_id("job")
        async with harness.sessions() as session:
            job: MapJobRow | None = await session.get(MapJobRow, "job")
            assert job is not None and job.status == MapJobStatus.SUCCEEDED.value
        assert monotonic() - started < 60
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_reduce_reconciles_after_clean_success() -> None:
    """A complete clean sweep resolves issues at its start watermark once."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        spec: MapJobSpec = _spec()
        async with harness.sessions.begin() as session:
            session.add(
                MapJobRow(
                    id="job",
                    origin=MapOrigin.COLLECTOR.value,
                    step_run_id=None,
                    collector_config_id="collector-1",
                    spec_json=dump_json(spec_to_json(spec)),
                    status=MapJobStatus.RUNNING.value,
                    partition_done=True,
                    max_concurrency=32,
                    paused_until=None,
                    deadline=now + timedelta(days=1),
                    result_json=None,
                    created_at=now,
                    finished_at=None,
                )
            )
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
        await reducer._handle_id("job")
        async with harness.sessions() as session:
            job: MapJobRow | None = await session.get(MapJobRow, "job")
            assert job is not None and job.status == MapJobStatus.SUCCEEDED.value
        assert len(issues.calls) == 1
        assert issues.calls[0].replace(tzinfo=UTC) == now
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_reduce_skips_reconciliation_when_resource_scan_failed() -> None:
    """DR-1: resource failures yield partial and never auto-resolve unchecked issues."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        spec: MapJobSpec = _spec()
        async with harness.sessions.begin() as session:
            session.add(
                MapJobRow(
                    id="job",
                    origin=MapOrigin.COLLECTOR.value,
                    step_run_id=None,
                    collector_config_id="collector-1",
                    spec_json=dump_json(spec_to_json(spec)),
                    status=MapJobStatus.RUNNING.value,
                    partition_done=True,
                    max_concurrency=32,
                    paused_until=None,
                    deadline=now + timedelta(days=1),
                    result_json=None,
                    created_at=now,
                    finished_at=None,
                )
            )
            session.add(
                MapItemRow(
                    id="item",
                    map_job_id="job",
                    shard_key="shard",
                    descriptor_json='{"id":"opaque"}',
                    weight=1.0,
                    status=MapItemStatus.SUCCEEDED.value,
                    attempt=1,
                    dispatched_at=None,
                    finished_at=now,
                    summary_json='{"resources_failed":1,"resources_scanned":0}',
                    error_code=None,
                    police_task_id=None,
                    scan_job_id="scan",
                )
            )
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
        await reducer._handle_id("job")
        async with harness.sessions() as session:
            job: MapJobRow | None = await session.get(MapJobRow, "job")
            assert job is not None and job.status == MapJobStatus.PARTIAL.value
        assert issues.calls == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_map_handlers_register_and_accept_claimed_tasks() -> None:
    """Each map handler implements the engine registry contract by task kind."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
        clock: FakeClock = FakeClock(now)
        queue: QueueFake = QueueFake(set(), [])
        gateway: GatewayFake = GatewayFake()
        config: ConfigFake = ConfigFake(_config())
        handlers: tuple[TaskHandler, ...] = (
            MapPartitionHandler(
                harness.sessions,
                SweeperFake(),
                gateway,
                config,
                Redactor.default(),
                clock,
                queue,
            ),
            MapDispatchHandler(
                harness.sessions,
                queue,
                gateway,
                ScanJobsFake(),
                config,
                clock,
                2,
            ),
            MapShardHandler(harness.sessions, SweeperFake(), queue, clock),
            MapReduceHandler(
                harness.sessions,
                queue,
                gateway,
                IssueFake([]),
                SignalFake(),
                AuditFake(),
                clock,
            ),
        )
        expected: tuple[EngineTaskKind, ...] = (
            EngineTaskKind.MAP_PARTITION,
            EngineTaskKind.MAP_DISPATCH,
            EngineTaskKind.MAP_SHARD,
            EngineTaskKind.MAP_REDUCE,
        )
        assert tuple(handler.kind for handler in handlers) == expected
        for handler in handlers:
            claimed: ClaimedTask = ClaimedTask(
                "task",
                handler.kind,
                "missing-row",
                json_null(),
                1,
                3,
                f"task:{handler.kind.value}",
            )
            result: TaskResult = await handler.handle(claimed)
            assert result.kind is TaskResultKind.DONE
    finally:
        await harness.close()
