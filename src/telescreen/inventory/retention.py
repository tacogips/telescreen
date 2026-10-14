"""Scheduled retention notifications with an advancing keyset cursor."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from croniter import croniter
from sqlalchemy import or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import (
    DataAssetState,
    DataFieldRole,
    EngineTaskKind,
)
from telescreen.db.models import DataAssetRow, DataFieldRow, DataFieldValueRow
from telescreen.db.models.retention_handoff import InventoryRetentionOutboxRow
from telescreen.masking.redact import Redactor
from telescreen.ports.notification_types import NotificationContext
from telescreen.ports.retention import (
    RetentionCommand,
    encode_retention_command,
    retention_event_id,
)
from telescreen.ports.workflow import (
    ClaimedTask,
    ScheduleProvider,
    TaskHandler,
    TaskQueue,
    TaskResult,
    TaskSpec,
)

logger: logging.Logger = logging.getLogger(__name__)
_BATCH_SIZE: int = 200


@dataclass
class DataOverdueProvider(ScheduleProvider):
    """Enqueue one overdue check for each recent configured cron fire."""

    cron: str
    queue: TaskQueue
    last_tick_at: datetime | None = None

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Enqueue the scheduled task once per fire minute; empty cron disables it."""
        current: datetime = ensure_utc(now)
        if self.last_tick_at is not None and current - self.last_tick_at < timedelta(seconds=60):
            return 0
        self.last_tick_at = current
        if not self.cron:
            return 0
        fire: datetime = (
            current
            if croniter.match(self.cron, current)
            else ensure_utc(croniter(self.cron, current).get_prev(datetime))
        )
        if current - fire > timedelta(hours=1):
            return 0
        minute: datetime = fire.replace(second=0, microsecond=0)
        key: str = f"data-overdue:{minute.isoformat()}"
        # engine_tasks.ref_id is varchar(36); the ISO key only fits the idempotency key.
        ref_id: str = f"data-overdue:{int(minute.timestamp()) // 60}"
        return int(
            await self.queue.enqueue(
                session,
                TaskSpec(
                    kind=EngineTaskKind.DATA_OVERDUE_CHECK,
                    ref_id=ref_id,
                    payload=json_object((("fire_at", json_str(minute.isoformat())),)),
                    available_at=current,
                    idempotency_key=key,
                ),
            )
        )


@dataclass(frozen=True)
class _DueAsset:
    """One selected asset and its deletion date."""

    id: str
    due: str


@dataclass(frozen=True)
class DataOverdueHandler(TaskHandler):
    """Publish overdue obligations without accessing identity or notification tables."""

    sessions: async_sessionmaker[AsyncSession]
    redactor: Redactor
    clock: Clock
    public_base_url: str | None
    kind: EngineTaskKind = EngineTaskKind.DATA_OVERDUE_CHECK

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Walk due assets in stable batches; isolate publication failures per asset."""
        del task
        today: str = self.clock.now().date().isoformat()
        last_id: str = ""
        while True:
            async with self.sessions() as session:
                batch: tuple[_DueAsset, ...] = await self._batch(session, today, last_id)
            if not batch:
                return TaskResult.done()
            for item in batch:
                last_id = item.id
                try:
                    async with self.sessions.begin() as session:
                        await self._publish(session, item)
                except Exception as error:
                    code: str = (
                        error.code.value if isinstance(error, TelescreenError) else "unexpected"
                    )
                    logger.warning("data overdue publication failed code=%s", code)

    async def _batch(
        self, session: AsyncSession, today: str, last_id: str
    ) -> tuple[_DueAsset, ...]:
        """Select overdue and not-yet-published values with an advancing asset cursor."""
        rows: Sequence[Row[tuple[DataAssetRow, DataFieldValueRow]]] = (
            await session.execute(
                select(DataAssetRow, DataFieldValueRow)
                .join(DataFieldValueRow, DataFieldValueRow.asset_id == DataAssetRow.id)
                .join(DataFieldRow, DataFieldRow.id == DataFieldValueRow.field_id)
                .where(
                    DataFieldRow.role == DataFieldRole.DELETION_DUE.value,
                    DataFieldRow.archived_at.is_(None),
                    DataFieldValueRow.value_date.is_not(None),
                    DataFieldValueRow.value_date < today,
                    DataAssetRow.state != DataAssetState.DELETED.value,
                    or_(
                        DataAssetRow.overdue_notified_for.is_(None),
                        DataAssetRow.overdue_notified_for != DataFieldValueRow.value_date,
                    ),
                    DataAssetRow.id > last_id,
                )
                .order_by(DataAssetRow.id)
                .limit(_BATCH_SIZE)
            )
        ).all()
        return tuple(_DueAsset(row[0].id, row[1].value_date or "") for row in rows)

    async def _publish(self, session: AsyncSession, selected: _DueAsset) -> None:
        """Lock, recheck and atomically stage a masked revision-specific obligation."""
        asset: DataAssetRow | None = await session.get(
            DataAssetRow, selected.id, with_for_update=True
        )
        if asset is None or asset.state == DataAssetState.DELETED.value:
            return
        field: DataFieldRow | None = await session.scalar(
            select(DataFieldRow).where(
                DataFieldRow.role == DataFieldRole.DELETION_DUE.value,
                DataFieldRow.archived_at.is_(None),
            )
        )
        if field is None:
            return
        value: DataFieldValueRow | None = await session.get(DataFieldValueRow, (asset.id, field.id))
        if (
            value is None
            or value.value_date is None
            or value.value_date >= self.clock.now().date().isoformat()
            or asset.overdue_notified_for == value.value_date
        ):
            return
        event_id: str = retention_event_id(asset.id, field.id, value.revision_id)
        existing: InventoryRetentionOutboxRow | None = await session.get(
            InventoryRetentionOutboxRow, event_id
        )
        if existing is None:
            now: datetime = ensure_utc(self.clock.now())
            command: RetentionCommand = RetentionCommand(
                event_id,
                now,
                asset.id,
                field.id,
                value.revision_id,
                asset.source,
                asset.resource_key,
                NotificationContext(
                    data_short_id=f"DATA-{asset.number}",
                    data_name=self.redactor.redact(asset.name).text,
                    data_locator=self.redactor.redact(asset.display_locator).text,
                    data_deletion_due=value.value_date,
                    data_link=f"{self.public_base_url.rstrip('/')}/data/{asset.id}"
                    if self.public_base_url
                    else None,
                ),
            )
            session.add(
                InventoryRetentionOutboxRow(
                    id=event_id,
                    payload_json=encode_retention_command(command),
                    recorded_at=now,
                )
            )
        # Legacy name: this now records publication, never remote delivery success.
        asset.overdue_notified_for = value.value_date
        await session.flush()
