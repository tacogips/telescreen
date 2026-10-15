"""Serialized relay regression schedules for concurrent source changes."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.collectors.inventory_publish import InventoryPublisher
from telescreen.core.clock import FakeClock
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind, IssueStatus
from telescreen.db.models import ApiTokenRow, CounterRow, IssueRow, ProjectionChangeRow
from telescreen.db.models.inventory_handoff import InventoryOutboxRow
from telescreen.db.models.notification_handoff import NotificationOutboxRow
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.integrations.notification_outbox import NotificationOutbox
from telescreen.integrations.notification_publish import NotificationOutboxPublisher
from telescreen.ports.directory_projection import ApiTokenEntity
from telescreen.ports.inventory_commands import (
    DirectoryUpdate,
    InventoryCommand,
    InventoryOperation,
    IssueLinkState,
    decode_inventory_command,
)
from telescreen.ports.notification_commands import (
    DirectoryOperation,
    NotificationCommand,
    decode_notification_command,
)
from telescreen.ports.workflow import ClaimedTask
from telescreen.projections.markers import install_projection_markers, uninstall_projection_markers
from telescreen.projections.relay import ProjectionRelayHandler
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.support.db import SqliteHarness, make_sqlite_harness

_NOW: datetime = datetime(2026, 10, 16, tzinfo=UTC)


@dataclass(frozen=True)
class _TokenState:
    """Only-if-newer token replica state used to check out-of-order application."""

    revision: int
    revoked: bool


def _apply_token(state: _TokenState, revision: int, entity: ApiTokenEntity) -> _TokenState:
    """Ignore stale directory revisions and retain the newest value."""
    return _TokenState(revision, entity.revoked) if revision > state.revision else state


def _task() -> ClaimedTask:
    """Build the relay task envelope without writing queue state."""
    return ClaimedTask(
        "task-1",
        EngineTaskKind.PROJECTION_RELAY,
        "projection:1",
        json_null(),
        1,
        5,
        "projection-relay:1",
    )


@pytest.mark.asyncio
async def test_relay_token_revoke_and_issue_status_are_serialized_and_marker_id_scoped() -> None:
    """Keep late ORM markers for the next run and publish increasing revisions."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(_NOW)
    queue: SqlTaskQueue = SqlTaskQueue(clock)
    inventory: InventoryPublisher = InventoryPublisher(InventoryOutbox(), queue, clock)
    notifications: NotificationOutboxPublisher = NotificationOutboxPublisher(
        NotificationOutbox(), queue, clock
    )
    install_projection_markers(harness.sessions, clock)
    try:
        async with harness.sessions.begin() as session:
            session.add(CounterRow(name="directory_revision", value=0))
            session.add(
                ApiTokenRow(
                    id="token-1",
                    label="reader",
                    token_hash="a" * 64,
                    scopes="issues:read",
                    member_id=None,
                    created_at=_NOW,
                    revoked_at=None,
                )
            )

        async def revoke_after_read(session: AsyncSession) -> None:
            token: ApiTokenRow = await session.get_one(ApiTokenRow, "token-1")
            token.revoked_at = _NOW

        first: ProjectionRelayHandler = ProjectionRelayHandler(
            harness.sessions, inventory, notifications, queue, clock, revoke_after_read
        )
        await first.handle(_task())
        async with harness.sessions() as session:
            markers: tuple[ProjectionChangeRow, ...] = tuple(
                (await session.scalars(select(ProjectionChangeRow))).all()
            )
            assert len(markers) == 1
            first_rows: tuple[InventoryOutboxRow, ...] = tuple(
                (
                    await session.scalars(
                        select(InventoryOutboxRow)
                        .where(InventoryOutboxRow.stream_id == "directory")
                        .order_by(InventoryOutboxRow.sequence)
                    )
                ).all()
            )
            first_command: InventoryCommand = decode_inventory_command(first_rows[0].payload_json)
            first_operation: InventoryOperation = first_command.operation
            assert isinstance(first_operation, DirectoryUpdate)
            assert isinstance(first_operation.entity.payload, ApiTokenEntity)
            assert first_operation.entity.payload.revoked is False
            assert first_operation.entity.revision == 1

        second: ProjectionRelayHandler = ProjectionRelayHandler(
            harness.sessions, inventory, notifications, queue, clock
        )
        await second.handle(_task())
        async with harness.sessions() as session:
            rows: tuple[InventoryOutboxRow, ...] = tuple(
                (
                    await session.scalars(
                        select(InventoryOutboxRow)
                        .where(InventoryOutboxRow.stream_id == "directory")
                        .order_by(InventoryOutboxRow.sequence)
                    )
                ).all()
            )
            assert len(rows) == 2
            first_payload: InventoryOperation = decode_inventory_command(
                rows[0].payload_json
            ).operation
            second_payload: InventoryOperation = decode_inventory_command(
                rows[1].payload_json
            ).operation
            assert isinstance(first_payload, DirectoryUpdate)
            assert isinstance(second_payload, DirectoryUpdate)
            assert isinstance(first_payload.entity.payload, ApiTokenEntity)
            assert isinstance(second_payload.entity.payload, ApiTokenEntity)
            assert first_payload.entity.revision == 1
            assert second_payload.entity.revision == 2
            assert first_payload.entity.payload.revoked is False
            assert second_payload.entity.payload.revoked is True
            notification_rows: tuple[NotificationOutboxRow, ...] = tuple(
                (await session.scalars(select(NotificationOutboxRow))).all()
            )
            commands: tuple[NotificationCommand, ...] = tuple(
                decode_notification_command(row.payload_json) for row in notification_rows
            )
            assert len(commands) == 2
            assert all(
                isinstance(command.operations[0], DirectoryOperation) for command in commands
            )
            assert not (await session.scalars(select(ProjectionChangeRow))).all()

        initial: _TokenState = _TokenState(0, False)
        a_then_b: _TokenState = _apply_token(
            _apply_token(initial, 1, first_payload.entity.payload),
            2,
            second_payload.entity.payload,
        )
        b_then_a: _TokenState = _apply_token(
            _apply_token(initial, 2, second_payload.entity.payload),
            1,
            first_payload.entity.payload,
        )
        assert a_then_b == b_then_a == _TokenState(2, True)

        async with harness.sessions.begin() as session:
            session.add(
                IssueRow(
                    id="issue-1",
                    number=41,
                    grouping_key="group-issue-1",
                    source="github",
                    resource_key="resource-key",
                    display_locator="safe-locator",
                    display_title="safe title",
                    rule_class="credential",
                    severity="high",
                    status=IssueStatus.UNRESOLVED.value,
                    first_seen=_NOW,
                    last_seen=_NOW,
                    last_checked_at=_NOW,
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )

        async def resolve_after_read(session: AsyncSession) -> None:
            issue: IssueRow = await session.get_one(IssueRow, "issue-1")
            issue.status = IssueStatus.RESOLVED.value

        await ProjectionRelayHandler(
            harness.sessions, inventory, notifications, queue, clock, resolve_after_read
        ).handle(_task())
        await second.handle(_task())
        async with harness.sessions() as session:
            issue_rows: tuple[InventoryOutboxRow, ...] = tuple(
                (
                    await session.scalars(
                        select(InventoryOutboxRow)
                        .where(InventoryOutboxRow.stream_id == "issue-link:issue-1")
                        .order_by(InventoryOutboxRow.sequence)
                    )
                ).all()
            )
            assert len(issue_rows) == 2
            issue_a: InventoryCommand = decode_inventory_command(issue_rows[0].payload_json)
            issue_b: InventoryCommand = decode_inventory_command(issue_rows[1].payload_json)
            assert isinstance(issue_a.operation, IssueLinkState)
            assert isinstance(issue_b.operation, IssueLinkState)
            assert issue_a.sequence < issue_b.sequence
            assert issue_a.operation.status == IssueStatus.UNRESOLVED.value
            assert issue_b.operation.status == IssueStatus.RESOLVED.value
            assert not (await session.scalars(select(ProjectionChangeRow))).all()
    finally:
        uninstall_projection_markers(harness.sessions)
        await harness.close()
