"""Execution of collector-backed map shards."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json, json_str, parse_json
from telescreen.core.vocabulary import EngineTaskKind, ErrorCode, MapItemStatus
from telescreen.db.models import MapItemRow, MapJobRow
from telescreen.ports.collectors import RateLimited
from telescreen.ports.sweeps import CollectorSweeper, ShardSpec, ShardSummary
from telescreen.ports.workflow import ClaimedTask, TaskQueue, TaskResult, TaskSpec
from telescreen.workflow.mapreduce.repository import summary_to_json
from telescreen.workflow.mapreduce.spec import MapJobSpec
from telescreen.workflow.mapreduce.utils import spec_from_json


@dataclass
class MapShardHandler:
    """Scan a shard outside the job lock and commit only its current attempt."""

    sessions: async_sessionmaker[AsyncSession]
    sweeper: CollectorSweeper
    queue: TaskQueue
    clock: Clock
    kind: EngineTaskKind = EngineTaskKind.MAP_SHARD

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Handle a claimed shard task through the durable queue contract."""
        await self._handle_id(task.ref_id)
        return TaskResult.done()

    async def _handle_id(self, item_id: str) -> None:
        """Run one shard, retaining only count summaries in the map tables."""
        async with self.sessions() as session:
            item: MapItemRow | None = await session.get(MapItemRow, item_id)
            job: MapJobRow | None = await session.get(MapJobRow, item.map_job_id) if item else None
            if item is None or job is None or item.status != MapItemStatus.DISPATCHED.value:
                return
            attempt: int = item.attempt
            descriptor: str = item.descriptor_json
            shard_key: str = item.shard_key
            config_id: str = job.collector_config_id
            scan_job_id: str = item.scan_job_id or ""
        shard: ShardSpec = ShardSpec(shard_key, parse_json(descriptor), 1)
        try:
            summary: ShardSummary = await self.sweeper.scan_shard(config_id, shard, scan_job_id)
        except RateLimited as error:
            async with self.sessions.begin() as session:
                current: MapItemRow | None = await session.get(MapItemRow, item_id)
                current_job: MapJobRow | None = await session.get(MapJobRow, job.id)
                if current is not None and current_job is not None and current.attempt == attempt:
                    current.status = MapItemStatus.READY.value
                    current.dispatched_at = None
                    current.attempt = max(0, current.attempt - 1)
                    delay: float = (
                        error.retry_after_seconds
                        if error.retry_after_seconds is not None
                        else min(300.0, 5.0 * (2 ** max(0, attempt - 1)))
                    )
                    current_job.paused_until = self.clock.now() + timedelta(seconds=delay)
                    await self._wake(session, current_job)
            return
        except Exception as error:
            code: str = (
                error.code.value
                if isinstance(error, TelescreenError)
                else ErrorCode.FETCH_FAILED.value
            )
            async with self.sessions.begin() as session:
                current: MapItemRow | None = await session.get(MapItemRow, item_id)
                current_job: MapJobRow | None = await session.get(MapJobRow, job.id)
                if current is not None and current_job is not None:
                    if (
                        current.status != MapItemStatus.DISPATCHED.value
                        or current.attempt != attempt
                    ):
                        return
                    spec: MapJobSpec = spec_from_json(current_job.spec_json)
                    current.status = (
                        MapItemStatus.FAILED.value
                        if current.attempt >= spec.shard_max_attempts
                        else MapItemStatus.READY.value
                    )
                    current.error_code = code
                    current.dispatched_at = None
                    current.finished_at = (
                        self.clock.now() if current.status == MapItemStatus.FAILED.value else None
                    )
                    await self._wake(session, current_job)
            return
        async with self.sessions.begin() as session:
            current = await session.get(MapItemRow, item_id)
            current_job = await session.get(MapJobRow, job.id)
            if current is None or current_job is None:
                return
            if current.status != MapItemStatus.DISPATCHED.value or current.attempt != attempt:
                return
            current.status = MapItemStatus.SUCCEEDED.value
            current.summary_json = dump_json(summary_to_json(summary))
            current.finished_at = self.clock.now()
            current.dispatched_at = None
            await self._wake(session, current_job)

    async def _wake(self, session: AsyncSession, job: MapJobRow) -> None:
        """Enqueue an idempotent immediate dispatch wake-up."""
        now: datetime = self.clock.now()
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.MAP_DISPATCH,
                job.id,
                json_str(job.id),
                now,
                f"mapdisp:{job.id}:{int(now.timestamp())}",
            ),
        )
