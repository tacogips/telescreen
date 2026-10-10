"""Issue exception and owner-digest scheduled maintenance."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from croniter import croniter
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.json import json_object
from telescreen.core.vocabulary import EngineTaskKind
from telescreen.issues.exceptions import ExceptionService
from telescreen.ports.workflow import ScheduleProvider, TaskQueue, TaskSpec


@dataclass
class ExceptionMaintenanceProvider(ScheduleProvider):
    """Expire due exceptions and enqueue recent owner-digest cron fires."""

    exceptions: ExceptionService
    queue: TaskQueue
    digest_cron: str | None = None
    last_tick_at: datetime | None = None

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Run once per minute per process and create due digest work idempotently."""
        current: datetime = ensure_utc(now)
        if self.last_tick_at is not None and current - self.last_tick_at < timedelta(seconds=60):
            return 0
        self.last_tick_at = current
        changed: int = await self.exceptions.expire_due(session, current)
        created: int = 0
        if self.digest_cron is not None:
            fire: datetime = croniter(self.digest_cron, current).get_prev(datetime)
            age: timedelta = current - ensure_utc(fire)
            if timedelta(0) <= age <= timedelta(hours=1):
                created = int(
                    await self.queue.enqueue(
                        session,
                        TaskSpec(
                            EngineTaskKind.OWNER_DIGEST,
                            "owner-digest",
                            json_object(()),
                            current,
                            f"digest:{int(fire.timestamp()) // 60}",
                        ),
                    )
                )
        return changed + created
