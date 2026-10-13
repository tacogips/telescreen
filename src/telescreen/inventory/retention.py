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
from telescreen.core.json import json_int, json_object, json_str, parse_json
from telescreen.core.vocabulary import (
    DataAssetState,
    DataFieldRole,
    EngineTaskKind,
    ErrorCode,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.db.models import DataAssetRow, DataFieldRow, DataFieldValueRow, TeamRow
from telescreen.directory.actors import actor_kind_for
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import AssigneeIds, RecipientResolver
from telescreen.ports.inventory import AssetOwnerResolver
from telescreen.ports.notifications import (
    MemberNotification,
    MemberNotifier,
    NotificationContext,
    NotificationRequest,
    NotificationSender,
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
    """Notify overdue asset owners once per due date in isolated transactions."""

    sessions: async_sessionmaker[AsyncSession]
    members: MemberNotifier
    sender: NotificationSender
    recipients: RecipientResolver
    owners: AssetOwnerResolver
    audit: AuditLog
    clock: Clock
    public_base_url: str | None
    kind: EngineTaskKind = EngineTaskKind.DATA_OVERDUE_CHECK

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Walk due assets in stable id batches; isolate failures per asset."""
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
                        await self._notify(session, item)
                except Exception as error:
                    code: str = (
                        error.code.value if isinstance(error, TelescreenError) else "unexpected"
                    )
                    logger.warning("data overdue notification failed code=%s", code)

    async def _batch(
        self, session: AsyncSession, today: str, last_id: str
    ) -> tuple[_DueAsset, ...]:
        """Read the next 200 due and not-yet-notified assets by primary key."""
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
        selected: list[_DueAsset] = []
        for row in rows:
            asset: DataAssetRow = row[0]
            value: DataFieldValueRow = row[1]
            selected.append(_DueAsset(asset.id, value.value_date or ""))
        return tuple(selected)

    async def _notify(self, session: AsyncSession, selected: _DueAsset) -> None:
        """Resolve a valid owner or fallback, send, mark, and audit one asset."""
        asset: DataAssetRow | None = await session.get(
            DataAssetRow, selected.id, with_for_update=True
        )
        if asset is None:
            return
        due_field: DataFieldRow | None = await session.scalar(
            select(DataFieldRow).where(
                DataFieldRow.role == DataFieldRole.DELETION_DUE.value,
                DataFieldRow.archived_at.is_(None),
            )
        )
        if due_field is None:
            return
        due_value: DataFieldValueRow | None = await session.get(
            DataFieldValueRow, (asset.id, due_field.id)
        )
        if (
            due_value is None
            or due_value.value_date is None
            or due_value.value_date >= self.clock.now().date().isoformat()
        ):
            return
        due: str = due_value.value_date
        owner_field: DataFieldRow | None = await session.scalar(
            select(DataFieldRow).where(
                DataFieldRow.role == DataFieldRole.OWNER.value, DataFieldRow.archived_at.is_(None)
            )
        )
        owner_value: DataFieldValueRow | None = (
            await session.get(DataFieldValueRow, (asset.id, owner_field.id))
            if owner_field is not None
            else None
        )
        owner_ref: str | None = None
        if owner_value is not None:
            owner_ref = parse_json(owner_value.value_json).as_str()
        assignee: AssigneeIds | None = await self._assignee(session, owner_ref)
        if assignee is None:
            fallback: str | None = await self.owners.fallback_owner(
                session, asset.source, asset.resource_key, asset.display_locator
            )
            assignee = await self._assignee(session, fallback)
        context: NotificationContext = NotificationContext(
            data_short_id=f"DATA-{asset.number}",
            data_name=asset.name,
            data_locator=asset.display_locator,
            data_deletion_due=due,
            data_link=f"{self.public_base_url.rstrip('/')}/data/{asset.id}"
            if self.public_base_url
            else None,
        )
        count: int = 0
        recipient_kind: str = "none"
        if assignee is not None and assignee.member_id is not None:
            recipient_kind = "member"
            await self.members.send_to_member(
                session,
                MemberNotification(
                    member_id=assignee.member_id,
                    category=NotificationCategory.ASSIGNED,
                    event=NotificationEvent.DATA_OVERDUE,
                    context=context,
                ),
            )
            count = 1
        elif assignee is not None and assignee.team_id is not None:
            team_ref: str | None = await self._team_ref(session, assignee.team_id)
            if team_ref is not None:
                recipient_kind = "team"
                for channel in (NotificationChannel.EMAIL, NotificationChannel.SLACK):
                    await self.sender.send(
                        session,
                        NotificationRequest(
                            channel=channel,
                            recipients=(team_ref,),
                            subject="",
                            body="",
                            actor_ref="system:data-overdue",
                            event=NotificationEvent.DATA_OVERDUE,
                            template_name="system.data_overdue",
                            context=context,
                            record_unreachable=True,
                        ),
                    )
                    count += 1
        asset.overdue_notified_for = due
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind_for("system:data-overdue"),
                "system:data-overdue",
                "data_asset.overdue_notified",
                "data_asset",
                asset.id,
                "Overdue data notification recorded",
                json_object(
                    (
                        ("recipient_kind", json_str(recipient_kind)),
                        ("notification_count", json_int(count)),
                    )
                ),
            ),
        )

    async def _assignee(self, session: AsyncSession, ref: str | None) -> AssigneeIds | None:
        """Resolve only valid member or team refs; stale references use fallback."""
        if ref is None:
            return None
        kind: str
        separator: str
        subject: str
        kind, separator, subject = ref.partition(":")
        if not separator or not subject or kind not in ("member", "team"):
            return None
        try:
            return await self.recipients.assignee_of(session, ref)
        except TelescreenError as error:
            if error.code is ErrorCode.RECIPIENT_UNKNOWN:
                return None
            raise

    async def _team_ref(self, session: AsyncSession, team_id: str) -> str | None:
        """Resolve a validated team id to its slug reference for sender delivery."""
        team: TeamRow | None = await session.get(TeamRow, team_id)
        return f"team:{team.slug}" if team is not None else None
