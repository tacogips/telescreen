"""Daily retention cleanup for alert fire history."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.alerts.repository import AlertRepository
from telescreen.core.clock import Clock
from telescreen.ports.workflow import ScheduleProvider


@dataclass
class AlertMaintenanceProvider(ScheduleProvider):
    """Delete alert fire history older than 90 days once per day."""

    repository: AlertRepository
    clock: Clock
    last_run_at: datetime | None = None

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Prune old alert fires at most once during a UTC day."""
        if self.last_run_at is not None and now.date() <= self.last_run_at.date():
            return 0
        self.last_run_at = now
        return await self.repository.delete_fires_before(
            session, self.clock.now() - timedelta(days=90)
        )
