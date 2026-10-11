"""Collector health state, conditions, and open-transition notifications."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from croniter import croniter
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.collectors.coverage import CoverageRecorder
from telescreen.collectors.repository import CollectorRepository
from telescreen.core.clock import ensure_utc
from telescreen.core.json import json_int, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    HealthCondition,
    HealthStatus,
    NotificationChannel,
    NotificationEvent,
    ScanJobStatus,
    TriggerMode,
)
from telescreen.db.models import CollectorConfigRow, CollectorHealthRow
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.workflow import ScheduleProvider

_WATCH_WINDOW: timedelta = timedelta(hours=12)
_MINUTE: timedelta = timedelta(seconds=60)


@dataclass(frozen=True)
class CollectorHealthView:
    """Safe collector health response with conditions and timestamps."""

    status: HealthStatus
    conditions: tuple[HealthCondition, ...]
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int
    last_event_at: datetime | None
    watch_expires_at: datetime | None
    event_only: bool


@dataclass(frozen=True)
class CollectorHealthRecorder:
    """Update one collector's aggregate health markers."""

    async def job_finished(
        self, session: AsyncSession, config_id: str, status: ScanJobStatus, now: datetime
    ) -> None:
        """Apply success/failure outcomes; partial outcomes do not change health."""
        if status is ScanJobStatus.PARTIAL:
            return
        row: CollectorHealthRow = await _row(session, config_id, now)
        if status is ScanJobStatus.SUCCEEDED:
            row.last_success_at = now
            row.consecutive_failures = 0
        else:
            row.last_failure_at = now
            row.consecutive_failures += 1
        row.updated_at = now

    async def event_received(self, session: AsyncSession, config_id: str, now: datetime) -> None:
        """Record the time an accepted provider event arrived."""
        row: CollectorHealthRow = await _row(session, config_id, now)
        row.last_event_at = now
        row.updated_at = now

    async def watch_started(
        self, session: AsyncSession, config_id: str, expires_at: datetime, now: datetime
    ) -> None:
        """Record the expiration time of an established provider watch."""
        row: CollectorHealthRow = await _row(session, config_id, now)
        row.watch_expires_at = expires_at
        row.updated_at = now


def health_view(
    row: CollectorHealthRow | None,
    config: CollectorConfigRow,
    public_unscannable: int,
    now: datetime,
    failure_threshold: int = 3,
    alert_unscannable_public: bool = True,
) -> CollectorHealthView:
    """Evaluate health conditions from collector configuration and aggregate state."""
    conditions: list[HealthCondition] = []
    last_success: datetime | None = _utc(row.last_success_at) if row is not None else None
    last_failure: datetime | None = _utc(row.last_failure_at) if row is not None else None
    last_event: datetime | None = _utc(row.last_event_at) if row is not None else None
    watch_expiry: datetime | None = _utc(row.watch_expires_at) if row is not None else None
    failures: int = row.consecutive_failures if row is not None else 0
    event_only: bool = (
        config.trigger_mode == TriggerMode.EVENT.value
        and config.cron is None
        and config.sweep_cron is None
    )
    if config.enabled and config.cron is not None and _is_stale(config, last_success, now):
        conditions.append(HealthCondition.STALE_SUCCESS)
    if watch_expiry is not None and watch_expiry <= now + _WATCH_WINDOW:
        conditions.append(HealthCondition.WATCH_EXPIRING)
    if failures >= failure_threshold:
        conditions.append(HealthCondition.CONSECUTIVE_FAILURES)
    if alert_unscannable_public and public_unscannable > 0:
        conditions.append(HealthCondition.UNSCANNABLE_PUBLIC)
    if conditions:
        status: HealthStatus = HealthStatus.FAILING
    elif event_only:
        status = HealthStatus.WARNING
    elif row is None or (last_success is None and last_failure is None):
        status = HealthStatus.UNKNOWN
    else:
        status = HealthStatus.OK
    return CollectorHealthView(
        status,
        tuple(conditions),
        last_success,
        last_failure,
        failures,
        last_event,
        watch_expiry,
        event_only,
    )


async def list_health(
    session: AsyncSession,
    configs: tuple[CollectorConfigRow, ...],
    now: datetime,
    failure_threshold: int = 3,
    alert_unscannable_public: bool = True,
) -> tuple[tuple[str, CollectorHealthView], ...]:
    """Build health views for config rows using count-only coverage lookups."""
    results: list[tuple[str, CollectorHealthView]] = []
    for config in configs:
        row: CollectorHealthRow | None = await session.get(CollectorHealthRow, config.id)
        count: int = 0
        if alert_unscannable_public:
            from telescreen.collectors.coverage import CoverageRecorder

            count = await CoverageRecorder().public_unscannable_count(session, config.id)
        results.append(
            (
                config.id,
                health_view(
                    row,
                    config,
                    count,
                    now,
                    failure_threshold,
                    alert_unscannable_public,
                ),
            )
        )
    return tuple(results)


@dataclass
class CollectorHealthProvider(ScheduleProvider):
    """Evaluate collector health and notify once when a condition opens."""

    configs: CollectorRepository
    coverage: CoverageRecorder
    audit: AuditLog
    notifier: NotificationSender | None = None
    recipients: tuple[str, ...] = ()
    failure_threshold: int = 3
    alert_unscannable_public: bool = True
    last_tick_at: datetime | None = None

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Open newly observed conditions at most once per minute per process."""
        if self.last_tick_at is not None and now - self.last_tick_at < _MINUTE:
            return 0
        self.last_tick_at = now
        opened_count: int = 0
        rows: tuple[CollectorConfigRow, ...] = await self.configs.enabled(session)
        for config in rows:
            state: CollectorHealthRow = await _row(session, config.id, now)
            public_count: int = (
                await self.coverage.public_unscannable_count(session, config.id)
                if self.alert_unscannable_public
                else 0
            )
            view: CollectorHealthView = health_view(
                state,
                config,
                public_count,
                now,
                self.failure_threshold,
                self.alert_unscannable_public,
            )
            current: tuple[str, ...] = tuple(condition.value for condition in view.conditions)
            previous: tuple[str, ...] = tuple(
                value for value in state.open_conditions.split(",") if value
            )
            newly_opened: tuple[str, ...] = tuple(
                value for value in current if value not in previous
            )
            state.open_conditions = ",".join(current)
            if not newly_opened:
                continue
            state.alerted_at = now
            state.updated_at = now
            for condition_value in newly_opened:
                condition: HealthCondition = HealthCondition(condition_value)
                await self._notify(session, config, state, condition, public_count)
                await self.audit.append(
                    session,
                    AuditEntry(
                        ActorKind.SYSTEM,
                        "system:collector-health",
                        "health.alert_opened",
                        "collector",
                        config.id,
                        "collector health condition opened",
                        json_object(
                            (
                                ("condition", json_str(condition.value)),
                                ("consecutive_failures", json_int(state.consecutive_failures)),
                                ("public_unscannable", json_int(public_count)),
                            )
                        ),
                    ),
                )
                opened_count += 1
        return opened_count

    async def _notify(
        self,
        session: AsyncSession,
        config: CollectorConfigRow,
        state: CollectorHealthRow,
        condition: HealthCondition,
        public_count: int,
    ) -> None:
        """Send a masked health alert through each configured notification channel."""
        if self.notifier is None or not self.recipients:
            return
        body: str = (
            f"kind={config.kind}; condition={condition.value}; "
            f"last_success_at={_iso(state.last_success_at)}; "
            f"last_failure_at={_iso(state.last_failure_at)}; "
            f"consecutive_failures={state.consecutive_failures}; "
            f"public_unscannable={public_count}"
        )
        for channel in NotificationChannel:
            await self.notifier.send(
                session,
                NotificationRequest(
                    channel,
                    self.recipients,
                    "",
                    "",
                    "system:collector-health",
                    event=NotificationEvent.COLLECTOR_HEALTH_ALERT,
                    context=NotificationContext(
                        health_target=config.name,
                        health_condition=condition.value,
                        health_details=body,
                    ),
                ),
            )


async def _row(session: AsyncSession, config_id: str, now: datetime) -> CollectorHealthRow:
    """Get or create one collector health record in the caller transaction."""
    row: CollectorHealthRow | None = await session.get(CollectorHealthRow, config_id)
    if row is None:
        row = CollectorHealthRow(
            collector_config_id=config_id,
            last_success_at=None,
            last_failure_at=None,
            consecutive_failures=0,
            last_event_at=None,
            watch_expires_at=None,
            open_conditions="",
            alerted_at=None,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
    return row


def _is_stale(config: CollectorConfigRow, last_success: datetime | None, now: datetime) -> bool:
    """Check for two missed cron intervals, excluding newly created configs."""
    if config.cron is None:
        return False
    next_fire: datetime = croniter(config.cron, now).get_next(datetime)
    previous_fire: datetime = croniter(config.cron, next_fire).get_prev(datetime)
    interval: timedelta = next_fire - previous_fire
    created_at: datetime = ensure_utc(config.created_at)
    current: datetime = ensure_utc(now)
    if current - created_at < interval * 2:
        return False
    return last_success is None or current - last_success >= interval * 2


def _utc(value: datetime | None) -> datetime | None:
    """Normalize a stored timestamp to UTC."""
    return ensure_utc(value) if value is not None else None


def _iso(value: datetime | None) -> str:
    """Format a nullable timestamp for a safe health notification."""
    return ensure_utc(value).isoformat() if value is not None else "none"
