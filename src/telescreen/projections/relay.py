"""Serialized relay from ORM change markers to durable service outboxes."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.collectors.inventory_publish import InventoryPublisher
from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind
from telescreen.db.models import (
    CounterRow,
    ProjectionChangeRow,
)
from telescreen.integrations.notification_publish import NotificationOutboxPublisher
from telescreen.ports.directory_projection import (
    DirectoryEntity,
    DirectoryEntityKind,
    for_inventory,
)
from telescreen.ports.inventory_commands import DirectoryUpdate, IssueLinkState
from telescreen.ports.workflow import (
    ClaimedTask,
    ScheduleProvider,
    TaskHandler,
    TaskQueue,
    TaskResult,
    TaskSpec,
)
from telescreen.projections.entities import read_directory_entity, read_issue_link


@dataclass
class ProjectionRelayProvider(ScheduleProvider):
    """Wake the relay once per UTC minute while projection markers exist."""

    queue: TaskQueue
    last_tick_at: datetime | None = None

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Schedule a bounded relay run only when committed marker rows exist."""
        current: datetime = ensure_utc(now)
        if self.last_tick_at is not None and current - self.last_tick_at < timedelta(seconds=60):
            return 0
        self.last_tick_at = current
        pending: bool = bool(
            await session.scalar(select(exists().where(ProjectionChangeRow.id.is_not(None))))
        )
        if not pending:
            return 0
        minute: int = int(current.timestamp()) // 60
        return int(
            await self.queue.enqueue(
                session,
                TaskSpec(
                    EngineTaskKind.PROJECTION_RELAY,
                    f"projection:{minute}",
                    json_null(),
                    current,
                    f"projection-relay:{minute}",
                ),
            )
        )


@dataclass(frozen=True)
class ProjectionRelayHandler(TaskHandler):
    """Publish one locked marker snapshot and preserve markers added after its read."""

    sessions: async_sessionmaker[AsyncSession]
    inventory: InventoryPublisher
    notifications: NotificationOutboxPublisher
    queue: TaskQueue
    clock: Clock
    after_read: Callable[[AsyncSession], Awaitable[None]] | None = None
    kind: EngineTaskKind = EngineTaskKind.PROJECTION_RELAY

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Drain at most 200 markers in one lock-before-read transaction."""
        del task
        more: bool = False
        async with self.sessions.begin() as session:
            await session.execute(
                update(CounterRow)
                .where(CounterRow.name == "directory_revision")
                .values(value=CounterRow.value)
            )
            counter: CounterRow | None = await session.scalar(
                select(CounterRow).where(CounterRow.name == "directory_revision").with_for_update()
            )
            if counter is None:
                raise RuntimeError("directory revision counter is missing")
            markers: tuple[ProjectionChangeRow, ...] = tuple(
                (
                    await session.scalars(
                        select(ProjectionChangeRow)
                        .order_by(ProjectionChangeRow.recorded_at, ProjectionChangeRow.id)
                        .limit(200)
                    )
                ).all()
            )
            marker_ids: tuple[str, ...] = tuple(marker.id for marker in markers)
            unique_list: list[tuple[str, str]] = []
            for marker in markers:
                identity: tuple[str, str] = (marker.kind, marker.entity_ref)
                if identity not in unique_list:
                    unique_list.append(identity)
            unique: tuple[tuple[str, str], ...] = tuple(unique_list)
            directory: list[tuple[DirectoryEntityKind, str]] = []
            issues: list[str] = []
            for marker_kind, entity_ref in unique:
                entity_kind: str
                separator: str
                entity_id: str
                entity_kind, separator, entity_id = entity_ref.partition(":")
                if not separator:
                    continue
                if marker_kind == "issue_link":
                    issues.append(entity_id)
                else:
                    try:
                        directory.append((DirectoryEntityKind(entity_kind), entity_id))
                    except ValueError:
                        continue
            entities: list[DirectoryEntity] = []
            issue_links: list[IssueLinkState] = []
            revision: int = counter.value
            for entity_kind, entity_id in directory:
                revision += 1
                entities.append(
                    await read_directory_entity(session, entity_kind, entity_id, revision)
                )
            for issue_id in issues:
                issue_links.append(await read_issue_link(session, issue_id))
            if self.after_read is not None:
                await self.after_read(session)
            for entity in entities:
                await self.inventory.emit_operation(
                    session, "directory", DirectoryUpdate(for_inventory(entity))
                )
            for operation in issue_links:
                await self.inventory.emit_operation(
                    session, f"issue-link:{operation.issue_id}", operation
                )
            notification_entities: tuple[DirectoryEntity, ...] = tuple(
                entity
                for entity in entities
                if entity.kind
                in (
                    DirectoryEntityKind.MEMBER,
                    DirectoryEntityKind.TEAM,
                    DirectoryEntityKind.IDENTITY,
                    DirectoryEntityKind.API_TOKEN,
                )
            )
            await self.notifications.publish_directory(session, notification_entities)
            counter.value = revision
            if marker_ids:
                await session.execute(
                    delete(ProjectionChangeRow).where(ProjectionChangeRow.id.in_(marker_ids))
                )
            more = bool(
                await session.scalar(select(exists().where(ProjectionChangeRow.id.is_not(None))))
            )
        if more:
            now: datetime = self.clock.now()
            minute: int = int(ensure_utc(now).timestamp()) // 60
            async with self.sessions.begin() as session:
                await self.queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.PROJECTION_RELAY,
                        f"projection:{minute}",
                        json_null(),
                        now,
                        f"projection-relay:{minute}",
                    ),
                )
        return TaskResult.done()
