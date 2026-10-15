"""Two-connection PostgreSQL relay serialization integration test."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.collectors.inventory_publish import InventoryPublisher
from telescreen.core.clock import FakeClock
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind
from telescreen.db.models import ApiTokenRow, CounterRow, ProjectionChangeRow
from telescreen.db.models.inventory_handoff import InventoryOutboxRow
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.integrations.notification_outbox import NotificationOutbox
from telescreen.integrations.notification_publish import NotificationOutboxPublisher
from telescreen.ports.directory_projection import ApiTokenEntity
from telescreen.ports.inventory_commands import (
    DirectoryUpdate,
    InventoryOperation,
    decode_inventory_command,
)
from telescreen.ports.workflow import ClaimedTask, TaskResult
from telescreen.projections.markers import install_projection_markers, uninstall_projection_markers
from telescreen.projections.relay import ProjectionRelayHandler
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.support.owned_postgres import OwnedPostgres

pytest_plugins: tuple[str, ...] = ("tests.support.owned_postgres",)

pytestmark: list[pytest.MarkDecorator] = [
    pytest.mark.asyncio,
    pytest.mark.integration,
]


async def test_relay_waits_for_counter_lock_and_reads_committed_token_state(
    owned_postgres: OwnedPostgres,
) -> None:
    """A relay blocked behind another transaction reads its committed current state."""
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        owned_postgres.main, expire_on_commit=False
    )
    clock: FakeClock = FakeClock(datetime(2026, 10, 16, tzinfo=UTC))
    queue: SqlTaskQueue = SqlTaskQueue(clock)
    inventory: InventoryPublisher = InventoryPublisher(InventoryOutbox(), queue, clock)
    notifications: NotificationOutboxPublisher = NotificationOutboxPublisher(
        NotificationOutbox(), queue, clock
    )
    install_projection_markers(sessions, clock)
    try:
        async with sessions.begin() as session:
            token: ApiTokenRow = ApiTokenRow(
                id="pg-token-1",
                label="reader",
                token_hash="b" * 64,
                scopes="issues:read",
                member_id=None,
                created_at=clock.now(),
                revoked_at=None,
            )
            session.add(token)
            session.add(
                ProjectionChangeRow(
                    id="pg-marker-1",
                    kind="directory",
                    entity_ref="api_token:pg-token-1",
                    recorded_at=clock.now(),
                )
            )

        async with sessions() as blocker:
            async with blocker.begin():
                await blocker.execute(
                    update(CounterRow)
                    .where(CounterRow.name == "directory_revision")
                    .values(value=CounterRow.value)
                )
                token = await blocker.get_one(ApiTokenRow, "pg-token-1")
                token.revoked_at = clock.now()
                task: asyncio.Task[TaskResult] = asyncio.create_task(
                    ProjectionRelayHandler(sessions, inventory, notifications, queue, clock).handle(
                        ClaimedTask(
                            "pg-task-1",
                            EngineTaskKind.PROJECTION_RELAY,
                            "projection:1",
                            json_null(),
                            1,
                            5,
                            "projection-relay:1",
                        )
                    )
                )
                for _ in range(500):
                    async with owned_postgres.admin.connect() as admin:
                        waiting: int = int(
                            await admin.scalar(
                                text(
                                    "SELECT count(*) FROM pg_stat_activity "
                                    "WHERE usename = :role AND wait_event_type = 'Lock'"
                                ),
                                {"role": owned_postgres.main_schema},
                            )
                            or 0
                        )
                    if waiting > 0:
                        break
                    await asyncio.sleep(0.01)
                else:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise AssertionError("relay did not wait for the directory counter lock")
            result: TaskResult = await asyncio.wait_for(task, timeout=10)
            assert result.kind.value == "done"
        async with sessions() as session:
            rows: tuple[InventoryOutboxRow, ...] = tuple(
                (
                    await session.scalars(
                        select(InventoryOutboxRow).where(
                            InventoryOutboxRow.stream_id == "directory"
                        )
                    )
                ).all()
            )
            assert len(rows) == 1
            operation: InventoryOperation = decode_inventory_command(rows[0].payload_json).operation
            assert isinstance(operation, DirectoryUpdate)
            assert operation.entity.revision == 1
            assert isinstance(operation.entity.payload, ApiTokenEntity)
            assert operation.entity.payload.revoked is True
            assert not (await session.scalars(select(ProjectionChangeRow))).all()
    finally:
        uninstall_projection_markers(sessions)
