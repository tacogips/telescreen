"""Count-only reduction, removal reconciliation and map job controls."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, dump_json, json_array, json_int, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    EngineTaskKind,
    ErrorCode,
    MapItemStatus,
    MapJobStatus,
    MapPartitionStatus,
)
from telescreen.db.models import MapItemRow, MapJobRow, MapPartitionRow
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.inventory import InventoryReconciler, NoopInventoryReconciler
from telescreen.ports.issues import IssueRecorder
from telescreen.ports.police import PoliceGateway
from telescreen.ports.sweeps import ShardSummary
from telescreen.ports.workflow import (
    ClaimedTask,
    RunSignaler,
    StepSignal,
    StepSignalKind,
    TaskQueue,
    TaskResult,
    TaskSpec,
)
from telescreen.workflow.mapreduce.repository import aggregate_summaries
from telescreen.workflow.mapreduce.spec import MapJobSpec
from telescreen.workflow.mapreduce.utils import spec_from_json


@dataclass
class MapReduceHandler:
    """Finalize a job once and apply DR-1 removal reconciliation safely."""

    sessions: async_sessionmaker[AsyncSession]
    queue: TaskQueue
    gateway: PoliceGateway
    issues: IssueRecorder
    signaler: RunSignaler
    audit: AuditLog
    clock: Clock
    kind: EngineTaskKind = EngineTaskKind.MAP_REDUCE
    inventory: InventoryReconciler = field(default_factory=NoopInventoryReconciler)

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Handle a claimed reduction task through the durable queue contract."""
        await self._handle_id(task.ref_id)
        return TaskResult.done()

    async def _handle_id(self, job_id: str) -> None:
        """Aggregate a terminal sweep and publish only count-only output."""
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
                return
            summary: ShardSummary = await aggregate_summaries(session, job_id)
            failed_count: int = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MapItemRow)
                    .where(
                        MapItemRow.map_job_id == job_id,
                        MapItemRow.status == MapItemStatus.FAILED.value,
                    )
                )
                or 0
            )
            total: int = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MapItemRow)
                    .where(MapItemRow.map_job_id == job_id)
                )
                or 0
            )
            failed_partitions: list[MapPartitionRow] = list(
                await session.scalars(
                    select(MapPartitionRow).where(
                        MapPartitionRow.map_job_id == job_id,
                        MapPartitionRow.status == MapPartitionStatus.FAILED.value,
                    )
                )
            )
            spec: MapJobSpec = spec_from_json(job.spec_json)
            exceeds: bool = failed_count > spec.max_failed_items or (
                total > 0 and failed_count / total > spec.max_failed_ratio
            )
            timeout_item_id: str | None = await session.scalar(
                select(MapItemRow.id)
                .where(
                    MapItemRow.map_job_id == job_id,
                    MapItemRow.error_code == ErrorCode.SWEEP_TIMEOUT.value,
                )
                .limit(1)
            )
            timed_out: bool = (
                timeout_item_id is not None
                or ensure_utc(job.deadline) <= self.clock.now()
                or any(
                    partition.cursor == ErrorCode.SWEEP_TIMEOUT.value
                    for partition in failed_partitions
                )
            )
            status: MapJobStatus = (
                MapJobStatus.FAILED
                if failed_partitions or exceeds or timed_out
                else (
                    MapJobStatus.PARTIAL
                    if failed_count > 0 or summary.resources_failed > 0
                    else MapJobStatus.SUCCEEDED
                )
            )
            auto_resolved: int = summary.issues_auto_resolved
            if status is MapJobStatus.SUCCEEDED:
                auto_resolved += await self.issues.resolve_unchecked(
                    session, job.collector_config_id, job.created_at
                )
                await self.inventory.reconcile_sweep(
                    session, job.collector_config_id, job.created_at
                )
            failed_codes: list[tuple[str, int]] = []
            grouped_codes: list[tuple[str | None, int]] = list(
                (
                    await session.execute(
                        select(MapItemRow.error_code, func.count())
                        .where(
                            MapItemRow.map_job_id == job_id,
                            MapItemRow.status == MapItemStatus.FAILED.value,
                        )
                        .group_by(MapItemRow.error_code)
                    )
                )
                .tuples()
                .all()
            )
            failed_codes.extend((code, count) for code, count in grouped_codes if code is not None)
            if failed_partitions:
                for partition in failed_partitions:
                    code: str = partition.cursor or "fetch_failed"
                    existing: int = next(
                        (
                            index
                            for index, (candidate, _) in enumerate(failed_codes)
                            if candidate == code
                        ),
                        -1,
                    )
                    if existing < 0:
                        failed_codes.append((code, 1))
                    else:
                        failed_codes[existing] = (code, failed_codes[existing][1] + 1)
            if timed_out and not any(
                code == ErrorCode.SWEEP_TIMEOUT.value for code, _ in failed_codes
            ):
                failed_codes.append((ErrorCode.SWEEP_TIMEOUT.value, 1))
            failed_codes.sort(key=lambda entry: entry[0])
            status_json: str = status.value
            result: JsonValue = json_object(
                (
                    ("map_job_id", json_str(job.id)),
                    ("shards_total", json_int(total)),
                    ("shards_succeeded", json_int(max(0, total - failed_count))),
                    ("shards_failed", json_int(failed_count)),
                    ("resources_scanned", json_int(summary.resources_scanned)),
                    ("resources_failed", json_int(summary.resources_failed)),
                    ("findings_total", json_int(summary.findings_inserted)),
                    ("issues_opened", json_int(summary.issues_opened)),
                    ("issues_auto_resolved", json_int(auto_resolved)),
                    (
                        "findings_by_rule_class",
                        json_array(
                            tuple(
                                json_object(
                                    (
                                        ("rule_class", json_str(entry.rule_class.value)),
                                        ("count", json_int(entry.count)),
                                    )
                                )
                                for entry in summary.findings_by_rule_class
                            )
                        ),
                    ),
                    (
                        "failed_error_codes",
                        json_array(
                            tuple(
                                json_object((("code", json_str(code)), ("count", json_int(count))))
                                for code, count in failed_codes
                            )
                        ),
                    ),
                )
            )
            job.status = status_json
            job.result_json = dump_json(result)
            job.finished_at = self.clock.now()
            await self.audit.append(
                session,
                AuditEntry(
                    ActorKind.SYSTEM,
                    "system",
                    "map_job.finished",
                    "map_job",
                    job.id,
                    "Map job finished",
                    json_object((("status", json_str(status_json)),)),
                ),
            )
            if job.step_run_id is not None:
                await self.signaler.signal(
                    session,
                    job.step_run_id,
                    StepSignal(StepSignalKind.MAP_FINISHED, outcome=status_json, data=result),
                )

    async def cancel(self, session: AsyncSession, job_id: str) -> None:
        """Cancel ready work and outstanding Police tasks."""
        job: MapJobRow | None = await session.get(MapJobRow, job_id)
        if job is None or job.status in (
            MapJobStatus.SUCCEEDED.value,
            MapJobStatus.PARTIAL.value,
            MapJobStatus.FAILED.value,
            MapJobStatus.CANCELLED.value,
        ):
            return
        for item in await session.scalars(
            select(MapItemRow).where(
                MapItemRow.map_job_id == job_id,
                MapItemRow.status.in_((MapItemStatus.READY.value, MapItemStatus.DISPATCHED.value)),
            )
        ):
            if item.police_task_id is not None:
                await self.gateway.cancel(session, item.police_task_id)
            item.status = MapItemStatus.CANCELLED.value
            await self.queue.cancel_by_ref(session, item.id)
        for partition in await session.scalars(
            select(MapPartitionRow).where(
                MapPartitionRow.map_job_id == job_id,
                MapPartitionRow.status.in_(
                    (MapPartitionStatus.READY.value, MapPartitionStatus.LEASED.value)
                ),
            )
        ):
            if partition.police_task_id is not None:
                await self.gateway.cancel(session, partition.police_task_id)
            partition.status = MapPartitionStatus.FAILED.value
            partition.cursor = "cancelled"
            partition.police_task_id = None
            await self.queue.cancel_by_ref(session, partition.id)
        job.status = MapJobStatus.CANCELLED.value
        job.finished_at = self.clock.now()
        await self.queue.cancel_by_ref(session, job.id)

    async def retry_failed(self, session: AsyncSession, job_id: str) -> None:
        """Reopen a job by resetting failed shards and failed partitions."""
        job: MapJobRow | None = await session.get(MapJobRow, job_id)
        if job is None:
            return
        for item in await session.scalars(
            select(MapItemRow).where(
                MapItemRow.map_job_id == job_id, MapItemRow.status == MapItemStatus.FAILED.value
            )
        ):
            item.status = MapItemStatus.READY.value
            item.error_code = None
        failed_partitions: list[MapPartitionRow] = list(
            await session.scalars(
                select(MapPartitionRow).where(
                    MapPartitionRow.map_job_id == job_id,
                    MapPartitionRow.status == MapPartitionStatus.FAILED.value,
                )
            )
        )
        for partition in failed_partitions:
            await self.queue.cancel_by_ref(session, partition.id)
            partition.id = new_id()
            partition.status = MapPartitionStatus.READY.value
            partition.cursor = None
            partition.attempt = 1
            partition.police_task_id = None
            await self.queue.enqueue(
                session,
                TaskSpec(
                    EngineTaskKind.MAP_PARTITION,
                    partition.id,
                    json_str(partition.id),
                    self.clock.now(),
                    f"mappart:{partition.id}:1:root",
                ),
            )
        job.status = MapJobStatus.RUNNING.value
        job.finished_at = None
        job.result_json = None
        job.partition_done = not failed_partitions
        now: datetime = self.clock.now()
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.MAP_DISPATCH,
                job.id,
                json_str(job.id),
                now,
                f"mapdisp:{job.id}:retry:{new_id()}",
            ),
        )
