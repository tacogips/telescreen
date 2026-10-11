"""Health evaluation and silence alerts for Police workers."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    HealthCondition,
    HealthStatus,
    NotificationChannel,
    NotificationEvent,
    PoliceTaskStatus,
    PoliceWorkerStatus,
)
from telescreen.db.models import PoliceTaskRow, PoliceWorkerRow
from telescreen.police.gateway import Capability, capability
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.workflow import ScheduleProvider


@dataclass(frozen=True)
class WorkerHealth:
    """Safe health status for a registered Police worker."""

    status: HealthStatus
    conditions: tuple[HealthCondition, ...] = ()


def worker_health(
    worker_row: PoliceWorkerRow,
    queued_matching_since: datetime | None,
    now: datetime,
) -> WorkerHealth:
    """Calculate worker health from liveness and matching queued work."""
    if worker_row.status != PoliceWorkerStatus.ACTIVE.value:
        return WorkerHealth(HealthStatus.UNKNOWN)
    if worker_row.mode == "push":
        return WorkerHealth(
            HealthStatus.OK if worker_row.last_delivery_at is not None else HealthStatus.UNKNOWN
        )
    if worker_row.mode != "pull":
        return WorkerHealth(HealthStatus.UNKNOWN)
    current: datetime = ensure_utc(now)
    heartbeat: datetime | None = (
        ensure_utc(worker_row.last_heartbeat_at)
        if worker_row.last_heartbeat_at is not None
        else None
    )
    stale: bool = heartbeat is None or heartbeat < current - timedelta(minutes=5)
    if not stale:
        return WorkerHealth(HealthStatus.OK)
    old_matching_task: bool = queued_matching_since is not None and ensure_utc(
        queued_matching_since
    ) < current - timedelta(minutes=5)
    if old_matching_task:
        return WorkerHealth(HealthStatus.FAILING, (HealthCondition.POLICE_SILENT,))
    return WorkerHealth(HealthStatus.WARNING)


async def _worker_health_rows(
    session: AsyncSession, now: datetime
) -> tuple[tuple[PoliceWorkerRow, WorkerHealth], ...]:
    """Evaluate every worker against its oldest matching queued task."""
    workers_result: ScalarResult[PoliceWorkerRow] = await session.scalars(
        select(PoliceWorkerRow).order_by(PoliceWorkerRow.id)
    )
    task_result: ScalarResult[PoliceTaskRow] = await session.scalars(
        select(PoliceTaskRow)
        .where(PoliceTaskRow.status == PoliceTaskStatus.QUEUED.value)
        .order_by(PoliceTaskRow.created_at, PoliceTaskRow.id)
    )
    queued_tasks: tuple[PoliceTaskRow, ...] = tuple(task_result.all())
    rows: list[tuple[PoliceWorkerRow, WorkerHealth]] = []
    for worker in workers_result.all():
        worker_capability: Capability = capability(worker)
        matching_times: list[datetime] = [
            ensure_utc(task.created_at)
            for task in queued_tasks
            if task.task_type in worker_capability.task_types
            and (task.worker_id is None or task.worker_id == worker.id)
            and frozenset(filter(None, task.labels.split(","))).issubset(worker_capability.labels)
        ]
        matching_since: datetime | None = min(matching_times) if matching_times else None
        rows.append((worker, worker_health(worker, matching_since, now)))
    return tuple(rows)


async def list_worker_health(
    session: AsyncSession, now: datetime
) -> tuple[tuple[str, WorkerHealth], ...]:
    """Return safe worker health pairs for the Police list router."""
    rows: tuple[tuple[PoliceWorkerRow, WorkerHealth], ...] = await _worker_health_rows(session, now)
    return tuple((worker.id, health) for worker, health in rows)


@dataclass
class PoliceHealthProvider(ScheduleProvider):
    """Open and clear Police silence alerts on a bounded schedule."""

    audit: AuditLog
    clock: Clock
    notifier: NotificationSender | None = None
    recipients: tuple[str, ...] = ()
    last_tick_at: datetime | None = None

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Evaluate health at most once per minute and record condition transitions."""
        current: datetime = ensure_utc(now)
        process_time: datetime = ensure_utc(self.clock.now())
        if self.last_tick_at is not None and process_time - self.last_tick_at < timedelta(
            seconds=60
        ):
            return 0
        self.last_tick_at = process_time
        rows: tuple[tuple[PoliceWorkerRow, WorkerHealth], ...] = await _worker_health_rows(
            session, current
        )
        changed: int = 0
        for worker, health in rows:
            is_silent: bool = HealthCondition.POLICE_SILENT in health.conditions
            if is_silent and worker.health_alerted_at is None:
                await self._open_alert(session, worker, current)
                worker.health_alerted_at = current
                changed += 1
            elif not is_silent and worker.health_alerted_at is not None:
                worker.health_alerted_at = None
                changed += 1
        return changed

    async def _open_alert(
        self, session: AsyncSession, worker: PoliceWorkerRow, now: datetime
    ) -> None:
        """Send safe channel notifications and audit one alert opening."""
        subject: str = f"Telescreen health: police {worker.name} police_silent"
        if self.notifier is not None and self.recipients:
            for channel in (NotificationChannel.EMAIL, NotificationChannel.SLACK):
                await self.notifier.send(
                    session,
                    NotificationRequest(
                        channel,
                        self.recipients,
                        "",
                        "",
                        "system:police-health",
                        event=NotificationEvent.POLICE_HEALTH_ALERT,
                        context=NotificationContext(
                            health_target=worker.name,
                            health_condition=HealthCondition.POLICE_SILENT.value,
                        ),
                    ),
                )
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:police-health",
                "health.alert_opened",
                "police_worker",
                worker.id,
                subject,
                json_object(
                    (
                        ("worker_id", json_str(worker.id)),
                        ("condition", json_str(HealthCondition.POLICE_SILENT.value)),
                        ("opened_at", json_str(now.isoformat())),
                    )
                ),
            ),
        )
