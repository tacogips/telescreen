"""Concurrency-capped dispatch and timeout handling for map shards."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.json import JsonValue, json_object, json_str, parse_json
from telescreen.core.vocabulary import (
    EngineTaskKind,
    MapItemStatus,
    MapJobStatus,
    MapPartitionStatus,
    ScanTrigger,
)
from telescreen.db.models import MapItemRow, MapJobRow, MapPartitionRow
from telescreen.ports.collectors import (
    CollectorConfig,
    CollectorConfigReader,
    PoliceSelector,
    ScanJobStore,
)
from telescreen.ports.police import PoliceGateway, PoliceTaskRequest
from telescreen.ports.workflow import ClaimedTask, TaskQueue, TaskResult, TaskSpec
from telescreen.workflow.mapreduce.repository import in_flight_count
from telescreen.workflow.mapreduce.spec import MapJobSpec
from telescreen.workflow.mapreduce.utils import spec_from_json


@dataclass
class MapDispatchHandler:
    """Dispatch ready work while preserving the per-job database cap."""

    sessions: async_sessionmaker[AsyncSession]
    queue: TaskQueue
    gateway: PoliceGateway
    scan_jobs: ScanJobStore
    configs: CollectorConfigReader
    clock: Clock
    tick_seconds: int
    kind: EngineTaskKind = EngineTaskKind.MAP_DISPATCH

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Handle a claimed dispatcher task through the durable queue contract."""
        await self._handle_id(task.ref_id, task.idempotency_key)
        return TaskResult.done()

    async def _handle_id(self, job_id: str, dispatch_key: str | None = None) -> int:
        """Dispatch one tick and return the number of new shard tasks."""
        async with self.sessions.begin() as session:
            job: MapJobRow | None = await session.scalar(
                select(MapJobRow).where(MapJobRow.id == job_id).with_for_update()
            )
            if job is None or job.status in (
                MapJobStatus.SUCCEEDED.value,
                MapJobStatus.PARTIAL.value,
                MapJobStatus.FAILED.value,
                MapJobStatus.CANCELLED.value,
            ):
                return 0
            now: datetime = self.clock.now()
            spec: MapJobSpec = spec_from_json(job.spec_json)
            config: CollectorConfig | None = await self.configs.get(
                session, job.collector_config_id
            )
            if config is None and ensure_utc(job.deadline) > now:
                return 0
            if ensure_utc(job.deadline) <= now:
                for item in await session.scalars(
                    select(MapItemRow).where(
                        MapItemRow.map_job_id == job_id,
                        MapItemRow.status.in_(
                            (MapItemStatus.READY.value, MapItemStatus.DISPATCHED.value)
                        ),
                    )
                ):
                    if item.police_task_id is not None:
                        await self.gateway.cancel(session, item.police_task_id)
                    await self.queue.cancel_by_ref(session, item.id)
                    item.status = MapItemStatus.FAILED.value
                    item.error_code = "sweep_timeout"
                    item.police_task_id = None
                    item.finished_at = now
                if not job.partition_done:
                    partitions: list[MapPartitionRow] = list(
                        await session.scalars(
                            select(MapPartitionRow).where(
                                MapPartitionRow.map_job_id == job_id,
                                MapPartitionRow.status.in_(
                                    (
                                        MapPartitionStatus.READY.value,
                                        MapPartitionStatus.LEASED.value,
                                    )
                                ),
                            )
                        )
                    )
                    for partition in partitions:
                        if partition.police_task_id is not None:
                            await self.gateway.cancel(session, partition.police_task_id)
                        await self.queue.cancel_by_ref(session, partition.id)
                        partition.status = MapPartitionStatus.FAILED.value
                        partition.cursor = "sweep_timeout"
                        partition.police_task_id = None
                    job.partition_done = True
                await self.queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.MAP_REDUCE,
                        job.id,
                        json_str(job.id),
                        now,
                        f"mapreduce:{job.id}:{int(now.timestamp())}:{dispatch_key or 'timeout'}",
                    ),
                )
                return 0
            if config is None:
                return 0
            for item in await session.scalars(
                select(MapItemRow).where(
                    MapItemRow.map_job_id == job_id,
                    MapItemRow.status == MapItemStatus.DISPATCHED.value,
                )
            ):
                if (
                    item.dispatched_at is not None
                    and ensure_utc(item.dispatched_at) + spec.shard_timeout < now
                ):
                    if item.police_task_id is not None:
                        await self.gateway.cancel(session, item.police_task_id)
                    if item.attempt >= spec.shard_max_attempts:
                        item.status = MapItemStatus.FAILED.value
                        item.error_code = "sweep_timeout"
                        item.finished_at = now
                    else:
                        item.status = MapItemStatus.READY.value
                        item.error_code = None
                    await self.queue.cancel_by_ref(session, item.id)
                    item.police_task_id = None
            dispatched: int = await in_flight_count(session, job_id)
            slots: int = max(0, job.max_concurrency - dispatched)
            ready: list[MapItemRow] = list(
                await session.scalars(
                    select(MapItemRow)
                    .where(
                        MapItemRow.map_job_id == job_id,
                        MapItemRow.status == MapItemStatus.READY.value,
                    )
                    .order_by(MapItemRow.id)
                    .limit(slots)
                )
            )
            if job.paused_until is None or ensure_utc(job.paused_until) <= now:
                for item in ready:
                    if spec.map_via.value == "police":
                        selector: JsonValue = (
                            config.selector.input
                            if isinstance(config.selector, PoliceSelector)
                            else json_str("")
                        )
                        request: PoliceTaskRequest = PoliceTaskRequest(
                            spec.map_task_type or "scan_shard",
                            spec.map_labels,
                            json_object(
                                (
                                    ("sweep_id", json_str(job.id)),
                                    ("shard_key", json_str(item.shard_key)),
                                    ("descriptor", parse_json(item.descriptor_json)),
                                    ("selector", selector),
                                )
                            ),
                            now + spec.shard_timeout,
                            collector_config_id=job.collector_config_id,
                        )
                        item.police_task_id = await self.gateway.dispatch(session, request)
                    else:
                        if item.scan_job_id is None:
                            item.scan_job_id = await self.scan_jobs.create(
                                session,
                                job.collector_config_id,
                                config.kind.value,
                                ScanTrigger.SWEEP,
                                now,
                            )
                        await self.queue.enqueue(
                            session,
                            TaskSpec(
                                EngineTaskKind.MAP_SHARD,
                                item.id,
                                json_str(item.id),
                                now,
                                f"map:{job.id}:{item.shard_key}:{item.attempt + 1}",
                            ),
                        )
                    item.status = MapItemStatus.DISPATCHED.value
                    item.dispatched_at = now
                    item.attempt += 1
            count: int = (
                len(ready) if job.paused_until is None or ensure_utc(job.paused_until) <= now else 0
            )
            pending: str | None = await session.scalar(
                select(MapItemRow.id)
                .where(
                    MapItemRow.map_job_id == job.id,
                    MapItemRow.status.in_(
                        (MapItemStatus.READY.value, MapItemStatus.DISPATCHED.value)
                    ),
                )
                .limit(1)
            )
            if job.partition_done and pending is None:
                await self.queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.MAP_REDUCE,
                        job.id,
                        json_str(job.id),
                        now,
                        f"mapreduce:{job.id}:{int(now.timestamp())}:{dispatch_key or 'dispatch'}",
                    ),
                )
            else:
                tick: int = int(now.timestamp()) // max(1, self.tick_seconds)
                await self.queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.MAP_DISPATCH,
                        job.id,
                        json_str(job.id),
                        now + timedelta(seconds=self.tick_seconds),
                        f"mapdisp:{job.id}:{tick + 1}",
                    ),
                )
            return count
