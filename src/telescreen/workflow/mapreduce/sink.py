"""Police result sink for map partitions and shards."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.json import dump_json, json_str
from telescreen.core.vocabulary import (
    EngineTaskKind,
    ErrorCode,
    MapItemStatus,
    MapJobStatus,
    MapPartitionStatus,
)
from telescreen.db.models import MapItemRow, MapJobRow, MapPartitionRow
from telescreen.masking.redact import Redactor
from telescreen.ports.sweeps import MapResultSink, PartitionPage, ShardFailure, ShardSummary
from telescreen.ports.workflow import TaskQueue, TaskSpec
from telescreen.workflow.mapreduce.partition import MapPartitionHandler
from telescreen.workflow.mapreduce.repository import summary_to_json
from telescreen.workflow.mapreduce.spec import MapJobSpec
from telescreen.workflow.mapreduce.utils import spec_from_json


@dataclass(frozen=True)
class MapResultSinkImpl(MapResultSink):
    """Accept asynchronous Police results without trusting stale task ids."""

    queue: TaskQueue
    clock: Clock
    redactor: Redactor
    partitions: MapPartitionHandler

    async def partition_page(
        self, session: AsyncSession, police_task_id: str, page: PartitionPage
    ) -> None:
        """Apply a Police partition page to the matching active partition."""
        partition: MapPartitionRow | None = await session.scalar(
            select(MapPartitionRow).where(
                MapPartitionRow.police_task_id == police_task_id,
                MapPartitionRow.status == MapPartitionStatus.LEASED.value,
            )
        )
        if partition is None:
            return
        job: MapJobRow | None = await session.get(MapJobRow, partition.map_job_id)
        if job is None:
            return
        await self.partitions.apply_page(session, partition, page)
        pending: str | None = await session.scalar(
            select(MapPartitionRow.id)
            .where(
                MapPartitionRow.map_job_id == job.id,
                MapPartitionRow.status.in_(
                    (MapPartitionStatus.READY.value, MapPartitionStatus.LEASED.value)
                ),
            )
            .limit(1)
        )
        if pending is None:
            job.partition_done = True
            job.status = MapJobStatus.RUNNING.value
        await self._wake(session, job.id)

    async def shard_finished(
        self, session: AsyncSession, police_task_id: str, summary: ShardSummary
    ) -> None:
        """Persist count-only results for the matching dispatched Police shard."""
        item: MapItemRow | None = await session.scalar(
            select(MapItemRow).where(
                MapItemRow.police_task_id == police_task_id,
                MapItemRow.status == MapItemStatus.DISPATCHED.value,
            )
        )
        if item is None:
            return
        job: MapJobRow | None = await session.get(MapJobRow, item.map_job_id)
        item.status = MapItemStatus.SUCCEEDED.value
        item.summary_json = dump_json(summary_to_json(summary))
        item.finished_at = self.clock.now()
        item.police_task_id = None
        if job is not None:
            await self._wake(session, job.id)

    async def task_failed(
        self, session: AsyncSession, police_task_id: str, failure: ShardFailure
    ) -> None:
        """Retry or fail a Police shard without consuming attempts on 429."""
        item: MapItemRow | None = await session.scalar(
            select(MapItemRow).where(
                MapItemRow.police_task_id == police_task_id,
                MapItemRow.status == MapItemStatus.DISPATCHED.value,
            )
        )
        if item is None:
            partition: MapPartitionRow | None = await session.scalar(
                select(MapPartitionRow).where(
                    MapPartitionRow.police_task_id == police_task_id,
                    MapPartitionRow.status == MapPartitionStatus.LEASED.value,
                )
            )
            if partition is not None:
                job: MapJobRow | None = await session.get(MapJobRow, partition.map_job_id)
                partition.police_task_id = None
                if failure.error_code is ErrorCode.RATE_LIMITED:
                    partition.status = MapPartitionStatus.READY.value
                    if job is not None:
                        paused_until: datetime = self.clock.now() + timedelta(
                            seconds=failure.retry_after_seconds or 5
                        )
                        job.paused_until = paused_until
                        await self.queue.enqueue(
                            session,
                            TaskSpec(
                                EngineTaskKind.MAP_PARTITION,
                                partition.id,
                                json_str(partition.id),
                                paused_until,
                                f"mappart:{partition.id}:{partition.attempt}:rate-limit:{int(paused_until.timestamp())}",
                            ),
                        )
                else:
                    partition.status = (
                        MapPartitionStatus.FAILED.value
                        if partition.attempt >= 3
                        else MapPartitionStatus.READY.value
                    )
                    if partition.status == MapPartitionStatus.FAILED.value:
                        partition.cursor = failure.error_code.value
                    else:
                        partition.attempt += 1
                        await self.queue.enqueue(
                            session,
                            TaskSpec(
                                EngineTaskKind.MAP_PARTITION,
                                partition.id,
                                json_str(partition.id),
                                self.clock.now(),
                                f"mappart:{partition.id}:{partition.attempt}:retry",
                            ),
                        )
                if job is not None:
                    pending: str | None = await session.scalar(
                        select(MapPartitionRow.id)
                        .where(
                            MapPartitionRow.map_job_id == job.id,
                            MapPartitionRow.status.in_(
                                (MapPartitionStatus.READY.value, MapPartitionStatus.LEASED.value)
                            ),
                        )
                        .limit(1)
                    )
                    if pending is None:
                        job.partition_done = True
                        job.status = MapJobStatus.RUNNING.value
                    await self._wake(session, job.id)
            return
        job: MapJobRow | None = await session.get(MapJobRow, item.map_job_id)
        item.police_task_id = None
        if failure.error_code is ErrorCode.RATE_LIMITED:
            item.status = MapItemStatus.READY.value
            item.attempt = max(0, item.attempt - 1)
            if job is not None:
                job.paused_until = self.clock.now() + timedelta(
                    seconds=failure.retry_after_seconds or 5
                )
        else:
            spec: MapJobSpec | None = spec_from_json(job.spec_json) if job is not None else None
            item.status = (
                MapItemStatus.FAILED.value
                if spec is None or item.attempt >= spec.shard_max_attempts
                else MapItemStatus.READY.value
            )
            item.error_code = failure.error_code.value
        if job is not None:
            await self._wake(session, job.id)

    async def _wake(self, session: AsyncSession, job_id: str) -> None:
        """Queue the current dispatch tick once."""
        now: datetime = self.clock.now()
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.MAP_DISPATCH,
                job_id,
                json_str(job_id),
                now,
                f"mapdisp:{job_id}:{int(now.timestamp())}",
            ),
        )
