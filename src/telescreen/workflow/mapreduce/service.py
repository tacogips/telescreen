"""Creation and read helpers for durable map jobs."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, dump_json, json_float, json_int, json_object, json_str
from telescreen.core.vocabulary import (
    EngineTaskKind,
    MapJobStatus,
    MapOrigin,
    MapPartitionStatus,
)
from telescreen.db.models import MapJobRow, MapPartitionRow
from telescreen.ports.workflow import TaskQueue, TaskSpec
from telescreen.workflow.mapreduce.spec import MapJobSpec


@dataclass(frozen=True)
class MapJobService:
    """Create a job and its initial durable partition and dispatch work."""

    queue: TaskQueue
    clock: Clock

    async def create_job(
        self,
        session: AsyncSession,
        origin: MapOrigin,
        collector_config_id: str,
        step_run_id: str | None,
        spec: MapJobSpec,
    ) -> str:
        """Insert the job/root partition and enqueue the first tasks atomically."""
        now: datetime = self.clock.now()
        job_id: str = new_id()
        partition_id: str = new_id()
        job: MapJobRow = MapJobRow(
            id=job_id,
            origin=origin.value,
            step_run_id=step_run_id,
            collector_config_id=collector_config_id,
            spec_json=dump_json(spec_to_json(spec)),
            status=MapJobStatus.PARTITIONING.value,
            partition_done=False,
            max_concurrency=spec.max_concurrency,
            paused_until=None,
            deadline=now + spec.timeout,
            result_json=None,
            created_at=now,
            finished_at=None,
        )
        partition: MapPartitionRow = MapPartitionRow(
            id=partition_id,
            map_job_id=job_id,
            corpus_key="",
            cursor=None,
            status=MapPartitionStatus.READY.value,
            attempt=1,
            police_task_id=None,
        )
        session.add_all((job, partition))
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.MAP_PARTITION,
                partition_id,
                json_str(partition_id),
                now,
                f"mappart:{partition_id}:1:root",
            ),
        )
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
        return job_id


def spec_to_json(spec: MapJobSpec) -> JsonValue:
    """Serialize map policy using the closed JSON value model."""
    return json_object(
        (
            ("partition_via", json_str(spec.partition_via.value)),
            ("map_via", json_str(spec.map_via.value)),
            ("partition_task_type", json_str(spec.partition_task_type or "")),
            ("map_task_type", json_str(spec.map_task_type or "")),
            (
                "partition_labels",
                json_object(
                    tuple(
                        (str(index), json_str(value))
                        for index, value in enumerate(spec.partition_labels)
                    )
                ),
            ),
            (
                "map_labels",
                json_object(
                    tuple(
                        (str(index), json_str(value)) for index, value in enumerate(spec.map_labels)
                    )
                ),
            ),
            ("max_concurrency", json_int(spec.max_concurrency)),
            ("shard_timeout_seconds", json_float(spec.shard_timeout.total_seconds())),
            ("shard_max_attempts", json_int(spec.shard_max_attempts)),
            ("max_failed_ratio", json_float(spec.max_failed_ratio)),
            ("max_failed_items", json_int(spec.max_failed_items)),
            ("timeout_seconds", json_float(spec.timeout.total_seconds())),
        )
    )
