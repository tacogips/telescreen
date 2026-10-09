"""Police lease and task-deadline maintenance."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.vocabulary import ErrorCode, PoliceTaskStatus
from telescreen.db.models import PoliceTaskRow
from telescreen.ports.sweeps import MapResultSink, ShardFailure
from telescreen.ports.workflow import ScheduleProvider


@dataclass(frozen=True)
class PoliceMaintenance(ScheduleProvider):
    """Requeue expired leases and terminate work past its deadline."""

    map_sink: MapResultSink
    clock: Clock

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Apply due lease/deadline transitions and return changed rows."""
        current: datetime = ensure_utc(now)
        result: ScalarResult[PoliceTaskRow] = await session.scalars(
            select(PoliceTaskRow)
            .where(
                PoliceTaskRow.status.in_(
                    (
                        PoliceTaskStatus.QUEUED.value,
                        PoliceTaskStatus.LEASED.value,
                        PoliceTaskStatus.DELIVERED.value,
                    )
                )
            )
            .with_for_update(skip_locked=True)
        )
        changed: int = 0
        for task in result.all():
            if ensure_utc(task.deadline) <= current:
                task.status = PoliceTaskStatus.EXPIRED.value
                task.completed_at = current
                task.worker_id = None
                task.lease_expires_at = None
                changed += 1
                await self._fail_map_task(session, task)
            elif (
                task.status == PoliceTaskStatus.LEASED.value
                and task.lease_expires_at is not None
                and ensure_utc(task.lease_expires_at) <= current
            ):
                task.worker_id = None
                task.lease_expires_at = None
                if task.attempt < task.max_attempts:
                    task.status = PoliceTaskStatus.QUEUED.value
                else:
                    task.status = PoliceTaskStatus.FAILED.value
                    task.completed_at = current
                    await self._fail_map_task(session, task)
                changed += 1
        return changed

    async def _fail_map_task(self, session: AsyncSession, task: PoliceTaskRow) -> None:
        """Notify the sweep reducer when a map task expires or exhausts claims."""
        if task.task_type in {"partition", "scan_shard"}:
            await self.map_sink.task_failed(
                session,
                task.id,
                ShardFailure(ErrorCode.FETCH_FAILED, None),
            )
