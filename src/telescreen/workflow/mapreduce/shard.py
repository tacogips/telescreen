"""Execution of collector-backed map shards."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.json import dump_json, json_str, parse_json
from telescreen.core.vocabulary import EngineTaskKind, MapItemStatus
from telescreen.db.models import MapItemRow, MapJobRow
from telescreen.ports.collectors import RateLimited
from telescreen.ports.sweeps import CollectorSweeper, ShardSpec, ShardSummary
from telescreen.ports.workflow import TaskQueue, TaskSpec
from telescreen.workflow.mapreduce.repository import summary_to_json


@dataclass(frozen=True)
class MapShardHandler:
    """Scan a shard outside the job lock and commit only its current attempt."""

    sessions: async_sessionmaker[AsyncSession]
    sweeper: CollectorSweeper
    queue: TaskQueue
    clock: Clock

    async def handle(self, item_id: str) -> None:
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
                    current_job.paused_until = self.clock.now() + timedelta(
                        seconds=error.retry_after_seconds or 5
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
