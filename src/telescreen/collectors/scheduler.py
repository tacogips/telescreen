"""Schedule incremental scans, full sweeps, and watch renewals."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from croniter import croniter
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.collectors.codec import CursorState, cursor_from_json, cursor_to_json
from telescreen.collectors.police import PoliceCollector
from telescreen.collectors.repository import CollectorRepository
from telescreen.collectors.service import _task_payload
from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    CollectorKind,
    EngineTaskKind,
    ErrorCode,
    ScanTrigger,
    TriggerMode,
)
from telescreen.db.models import CollectorConfigRow
from telescreen.ports.collectors import CollectorConfig
from telescreen.ports.sweeps import SweepLauncher
from telescreen.ports.workflow import ScheduleProvider, TaskQueue, TaskSpec


@dataclass(frozen=True)
class CollectorScheduleProvider(ScheduleProvider):
    """Enqueue due collector work in the engine scheduler transaction."""

    queue: TaskQueue
    launcher: SweepLauncher
    police: PoliceCollector
    repository: CollectorRepository
    clock: Clock

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Run due cron scans and renew watches expiring within a day."""
        created: int = 0
        rows: tuple[CollectorConfigRow, ...] = await self.repository.enabled(session)
        for row in rows:
            config: CollectorConfig | None = await self.repository.get(session, row.id)
            if config is None:
                continue
            state: CursorState = cursor_from_json(row.cursor_json)
            if config.cron is not None and config.trigger_mode in (
                TriggerMode.SCHEDULE,
                TriggerMode.BOTH,
            ):
                fire: datetime = _latest_fire(config.cron, now)
                last_scheduled: datetime | None = (
                    ensure_utc(row.last_scheduled_at) if row.last_scheduled_at is not None else None
                )
                if last_scheduled is None or fire > last_scheduled:
                    if config.kind is CollectorKind.POLICE:
                        await self.police.dispatch_detect(session, config)
                    else:
                        created += int(
                            await self.queue.enqueue(
                                session,
                                TaskSpec(
                                    EngineTaskKind.COLLECTOR_SCAN,
                                    config.id,
                                    _task_payload(
                                        config.id, "changes", trigger=ScanTrigger.SCHEDULE
                                    ),
                                    now,
                                    f"collector:{config.id}:{fire.isoformat()}",
                                ),
                            )
                        )
                    row.last_scheduled_at = fire
            if config.sweep_cron is not None:
                fire_sweep: datetime = _latest_fire(config.sweep_cron, now)
                last_sweep_fire: datetime | None = (
                    ensure_utc(state.last_sweep_fire) if state.last_sweep_fire is not None else None
                )
                if last_sweep_fire is None or fire_sweep > last_sweep_fire:
                    try:
                        await self.launcher.start_collector_sweep(config.id, ScanTrigger.SCHEDULE)
                    except TelescreenError as error:
                        if error.code is not ErrorCode.CONFLICT:
                            raise
                    state = CursorState(state.cursor, state.watch, fire_sweep)
                    row.cursor_json = cursor_to_json(state)
                    created += 1
            if state.watch is not None and state.watch.expires_at <= now + timedelta(days=1):
                created += int(
                    await self.queue.enqueue(
                        session,
                        TaskSpec(
                            EngineTaskKind.COLLECTOR_WATCH_RENEW,
                            config.id,
                            _task_payload(config.id, "watch_renew"),
                            now,
                            f"watch:{config.id}:{state.watch.expires_at.isoformat()}",
                        ),
                    )
                )
        return created


def _latest_fire(expression: str, now: datetime) -> datetime:
    """Return the most recent cron fire at or before now."""
    return croniter(expression, now).get_prev(datetime)
