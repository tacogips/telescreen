"""Collector and Police partition handling with idempotent page writes."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, dump_json, json_object, json_str
from telescreen.core.vocabulary import EngineTaskKind, MapJobStatus, MapPartitionStatus
from telescreen.db.models import MapItemRow, MapJobRow, MapPartitionRow
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import (
    CollectorConfig,
    CollectorConfigReader,
    CollectorSelector,
    PoliceSelector,
)
from telescreen.ports.police import PoliceGateway, PoliceTaskRequest
from telescreen.ports.sweeps import CollectorSweeper, PartitionPage, PartitionRequest
from telescreen.ports.workflow import TaskQueue, TaskSpec
from telescreen.workflow.mapreduce.spec import MapJobSpec
from telescreen.workflow.mapreduce.utils import spec_from_json


@dataclass(frozen=True)
class MapPartitionHandler:
    """Handle a single durable partition page or Police partition request."""

    sessions: async_sessionmaker[AsyncSession]
    sweeper: CollectorSweeper
    gateway: PoliceGateway
    configs: CollectorConfigReader
    redactor: Redactor
    clock: Clock
    queue: TaskQueue

    async def handle(self, partition_id: str) -> None:
        """Partition through the selected adapter or enqueue a Police request."""
        async with self.sessions.begin() as session:
            partition: MapPartitionRow | None = await session.get(MapPartitionRow, partition_id)
            if partition is None or partition.status in (
                MapPartitionStatus.DONE.value,
                MapPartitionStatus.FAILED.value,
            ):
                return
            job: MapJobRow | None = await session.get(MapJobRow, partition.map_job_id)
            if job is None:
                return
            spec: MapJobSpec = spec_from_json(job.spec_json)
            config: CollectorConfig | None = await self.configs.get(
                session, job.collector_config_id
            )
            if config is None:
                partition.status = MapPartitionStatus.FAILED.value
                await self._complete_partitioning(session, job)
                return
            if spec.partition_via.value == "police":
                selector: JsonValue = (
                    config.selector.input
                    if isinstance(config.selector, PoliceSelector)
                    else json_str("")
                )
                payload: JsonValue = json_object(
                    (
                        ("sweep_id", json_str(job.id)),
                        ("corpus_key", json_str(partition.corpus_key)),
                        ("cursor", json_str(partition.cursor or "")),
                        ("selector", selector),
                    )
                )
                task_id: str = await self.gateway.dispatch(
                    session,
                    PoliceTaskRequest(
                        spec.partition_task_type or "partition",
                        spec.partition_labels,
                        payload,
                        self.clock.now() + spec.shard_timeout,
                        collector_config_id=job.collector_config_id,
                    ),
                )
                partition.status = MapPartitionStatus.LEASED.value
                partition.police_task_id = task_id
                return
            selector: CollectorSelector = config.selector
            request: PartitionRequest = PartitionRequest(
                partition.corpus_key, partition.cursor, selector
            )
        page: PartitionPage = await self.sweeper.partition(job.collector_config_id, request)
        async with self.sessions.begin() as session:
            row: MapPartitionRow | None = await session.get(MapPartitionRow, partition_id)
            current_job: MapJobRow | None = await session.get(MapJobRow, job.id)
            if (
                row is not None
                and current_job is not None
                and row.status != MapPartitionStatus.FAILED.value
            ):
                await self.apply_page(session, row, page)
                await self._complete_partitioning(session, current_job)

    async def apply_page(
        self, session: AsyncSession, partition: MapPartitionRow, page: PartitionPage
    ) -> None:
        """Persist a page idempotently and schedule continuation or child corpora."""
        job: MapJobRow | None = await session.get(MapJobRow, partition.map_job_id)
        if job is None:
            return
        if any(self.redactor.has_hits(dump_json(shard.descriptor)) for shard in page.shards):
            partition.status = MapPartitionStatus.FAILED.value
            partition.police_task_id = None
            return
        for shard in page.shards:
            try:
                async with session.begin_nested():
                    session.add(
                        MapItemRow(
                            id=new_id(),
                            map_job_id=job.id,
                            shard_key=shard.shard_key,
                            descriptor_json=dump_json(shard.descriptor),
                            weight=float(shard.weight),
                            status="ready",
                            attempt=0,
                            dispatched_at=None,
                            finished_at=None,
                            summary_json=None,
                            error_code=None,
                            police_task_id=None,
                            scan_job_id=None,
                        )
                    )
                    await session.flush()
            except IntegrityError:
                continue
        for child in page.sub_corpora:
            exists: str | None = await session.scalar(
                select(MapPartitionRow.id).where(
                    MapPartitionRow.map_job_id == job.id,
                    MapPartitionRow.corpus_key == child.corpus_key,
                )
            )
            if exists is None:
                child_id: str = new_id()
                session.add(
                    MapPartitionRow(
                        id=child_id,
                        map_job_id=job.id,
                        corpus_key=child.corpus_key,
                        cursor=None,
                        status=MapPartitionStatus.READY.value,
                        attempt=1,
                        police_task_id=None,
                    )
                )
                await self.queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.MAP_PARTITION,
                        child_id,
                        json_str(child_id),
                        self.clock.now(),
                        f"mappart:{child_id}:1:root",
                    ),
                )
        partition.cursor = page.next_cursor
        partition.police_task_id = None
        if page.next_cursor is None:
            partition.status = MapPartitionStatus.DONE.value
        else:
            partition.status = MapPartitionStatus.READY.value
            import hashlib

            cursor_hash: str = hashlib.sha256(page.next_cursor.encode()).hexdigest()[:16]
            key: str = f"mappart:{partition.id}:{partition.attempt}:{cursor_hash}"
            await self.queue.enqueue(
                session,
                TaskSpec(
                    EngineTaskKind.MAP_PARTITION,
                    partition.id,
                    json_str(partition.id),
                    self.clock.now(),
                    key,
                ),
            )

    async def _complete_partitioning(self, session: AsyncSession, job: MapJobRow) -> None:
        """Mark partitioning complete after all corpus rows reach terminal states."""
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
