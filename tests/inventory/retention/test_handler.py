"""Offline tests for overdue keyset processing and due-date idempotency."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.json import JsonValue, json_null
from telescreen.core.vocabulary import (
    DataAssetState,
    DataFieldRole,
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.db.models import (
    DataAssetRow,
    DataFieldRow,
    DataFieldValueRow,
    EngineTaskRow,
    TeamRow,
)
from telescreen.inventory.retention import DataOverdueHandler, DataOverdueProvider
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import AssigneeIds, RecipientResolver, ResolvedRecipients
from telescreen.ports.inventory import AssetOwnerResolver
from telescreen.ports.notifications import (
    MemberNotification,
    MemberNotifier,
    NotificationReceipt,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.workflow import ClaimedTask, TaskQueue, TaskResult, TaskResultKind, TaskSpec
from telescreen.workflow.queue.sql_queue import REF_ID_MAX_LENGTH, SqlTaskQueue
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class RecordingAudit(AuditLog):
    """Capture overdue audit records."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one audit entry."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class FakeRecipients(RecipientResolver):
    """Resolve current member and team refs and reject stale refs."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Return an empty delivery-address projection."""
        del session, ref
        return ResolvedRecipients((), (), (), ())

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Return false for unused membership checks."""
        del session, member_id, ref
        return False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Return the active member or reject a stale reference."""
        del session
        if ref == "member:active":
            return AssigneeIds("member-id", None)
        if ref == "team:ops":
            return AssigneeIds(None, "team-id")
        from telescreen.core.errors import TelescreenError

        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")


@dataclass
class FakeOwners(AssetOwnerResolver):
    """Supply a configured fallback reference."""

    fallback: str | None
    calls: int = 0

    async def fallback_owner(
        self, session: AsyncSession, source: str, resource_key: str, display_locator: str
    ) -> str | None:
        """Return the configured fallback and record that it was consulted."""
        del session, source, resource_key, display_locator
        self.calls += 1
        return self.fallback


@dataclass
class FakeMemberNotifier(MemberNotifier):
    """Capture member notification deliveries."""

    sent: list[MemberNotification] = field(default_factory=list)

    async def send_to_member(
        self, session: AsyncSession, notification: MemberNotification
    ) -> NotificationReceipt:
        """Record one member notification."""
        del session
        self.sent.append(notification)
        return NotificationReceipt(("notification-1",), DlpVerdict.PASS, True)


@dataclass
class FailingMemberNotifier(FakeMemberNotifier):
    """Record every attempt and fail for one selected data short id."""

    failed_data_short_id: str = "DATA-1"
    attempts: list[str] = field(default_factory=list)

    async def send_to_member(
        self, session: AsyncSession, notification: MemberNotification
    ) -> NotificationReceipt:
        """Raise after recording the configured failed delivery attempt."""
        data_short_id: str | None = notification.context.data_short_id
        assert data_short_id is not None
        self.attempts.append(data_short_id)
        if data_short_id == self.failed_data_short_id:
            raise RuntimeError("injected member send failure")
        return await super().send_to_member(session, notification)


@dataclass
class FakeSender(NotificationSender):
    """Capture team channel notification deliveries."""

    sent: list[NotificationRequest] = field(default_factory=list)

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Record one sender request."""
        del session
        self.sent.append(request)
        return NotificationReceipt((f"notification-{len(self.sent)}",), DlpVerdict.PASS, True)


@dataclass
class FakeQueue(TaskQueue):
    """Capture scheduled tasks."""

    tasks: list[TaskSpec] = field(default_factory=list)

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Record one idempotent task."""
        del session
        self.tasks.append(spec)
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Report that no task was cancelled."""
        del session, ref_id
        return 0


def _field(field_id: str, key: str, role: DataFieldRole, field_type: str) -> DataFieldRow:
    """Build a persisted metadata field."""
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    return DataFieldRow(
        id=field_id,
        key=key,
        label=key,
        description=None,
        field_type=field_type,
        options_json=None,
        required=False,
        position=0,
        role=role.value,
        archived_at=None,
        created_by="member:admin",
        created_at=now,
        updated_at=now,
    )


def _asset(asset_id: str, number: int, now: datetime) -> DataAssetRow:
    """Build one due present asset."""
    return DataAssetRow(
        id=asset_id,
        number=number,
        resource_key=f"{number:064x}",
        source="manual",
        origin="registered",
        kind="file",
        state=DataAssetState.PRESENT.value,
        name="Asset",
        display_locator="https://example.test",
        collector_config_id=None,
        sweep_covered=False,
        item_count=None,
        first_seen_at=now,
        last_seen_at=now,
        deleted_at=None,
        overdue_notified_for=None,
        created_by="member:admin",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_overdue_handler_falls_back_for_stale_owner_and_marks_once() -> None:
    """Stale owner refs use fallback; each due date is sent and audited only once."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, 12, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    members: FakeMemberNotifier = FakeMemberNotifier()
    sender: FakeSender = FakeSender()
    owners: FakeOwners = FakeOwners("member:active")
    handler: DataOverdueHandler = DataOverdueHandler(
        harness.sessions,
        members,
        sender,
        FakeRecipients(),
        owners,
        audit,
        FakeClock(now),
        "https://telescreen.example",
    )
    task: ClaimedTask = ClaimedTask(
        "task-1", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "due-key"
    )
    try:
        async with harness.sessions.begin() as session:
            asset: DataAssetRow = _asset("asset-1", 1, now)
            due: DataFieldRow = _field(
                "due-field", "deletion_due", DataFieldRole.DELETION_DUE, "date"
            )
            owner: DataFieldRow = _field("owner-field", "owner", DataFieldRole.OWNER, "reference")
            session.add_all((asset, due, owner))
            session.add_all(
                (
                    DataFieldValueRow(
                        asset_id=asset.id,
                        field_id=due.id,
                        value_json='"2026-10-12"',
                        value_date="2026-10-12",
                        updated_by="member:admin",
                        updated_at=now,
                    ),
                    DataFieldValueRow(
                        asset_id=asset.id,
                        field_id=owner.id,
                        value_json='"member:deleted"',
                        value_date=None,
                        updated_by="member:admin",
                        updated_at=now,
                    ),
                )
            )
        result: TaskResult = await handler.handle(task)
        assert result.kind is TaskResultKind.DONE
        assert len(members.sent) == 1
        assert members.sent[0].category is NotificationCategory.ASSIGNED
        assert members.sent[0].event is NotificationEvent.DATA_OVERDUE
        assert owners.calls == 1
        assert len(audit.entries) == 1
        async with harness.sessions() as session:
            asset_after: DataAssetRow | None = await session.get(DataAssetRow, "asset-1")
            assert asset_after is not None
            assert asset_after.overdue_notified_for == "2026-10-12"
        await handler.handle(task)
        assert len(members.sent) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_overdue_provider_enqueues_once_per_minute_and_empty_cron_disables() -> None:
    """The provider uses the scheduled UTC minute as its idempotency key."""
    harness: SqliteHarness = await make_sqlite_harness()
    queue: FakeQueue = FakeQueue()
    provider: DataOverdueProvider = DataOverdueProvider("0 9 * * *", queue)
    disabled: DataOverdueProvider = DataOverdueProvider("", queue)
    now: datetime = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    try:
        async with harness.sessions() as session:
            assert await provider.tick(session, now) == 1
            assert await provider.tick(session, now) == 0
            assert await disabled.tick(session, now) == 0
        assert queue.tasks[0].idempotency_key == "data-overdue:2026-10-14T09:00:00+00:00"
        assert len(queue.tasks[0].ref_id) <= REF_ID_MAX_LENGTH
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_overdue_provider_enqueues_through_the_sql_queue() -> None:
    """The scheduled task fits engine_tasks; PostgreSQL rejects a ref_id over 36 characters."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    provider: DataOverdueProvider = DataOverdueProvider("* * * * *", SqlTaskQueue(FakeClock(now)))
    try:
        async with harness.sessions.begin() as session:
            assert await provider.tick(session, now) == 1
        async with harness.sessions() as session:
            rows: tuple[EngineTaskRow, ...] = tuple(await session.scalars(select(EngineTaskRow)))
        assert [row.kind for row in rows] == [EngineTaskKind.DATA_OVERDUE_CHECK.value]
        assert rows[0].idempotency_key == "data-overdue:2026-10-14T09:00:00+00:00"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_overdue_team_owner_gets_email_and_slack_sends() -> None:
    """A fallback team owner receives the two required channel requests."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, 12, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    members: FakeMemberNotifier = FakeMemberNotifier()
    sender: FakeSender = FakeSender()
    owners: FakeOwners = FakeOwners("team:ops")
    handler: DataOverdueHandler = DataOverdueHandler(
        harness.sessions,
        members,
        sender,
        FakeRecipients(),
        owners,
        audit,
        FakeClock(now),
        None,
    )
    task: ClaimedTask = ClaimedTask(
        "task-2", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "due-key-2"
    )
    try:
        async with harness.sessions.begin() as session:
            session.add(
                TeamRow(
                    id="team-id",
                    slug="ops",
                    name="Operations",
                    email=None,
                    slack_channel=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            asset: DataAssetRow = _asset("asset-team", 2, now)
            due: DataFieldRow = _field(
                "due-team", "deletion_due", DataFieldRole.DELETION_DUE, "date"
            )
            session.add_all((asset, due))
            session.add(
                DataFieldValueRow(
                    asset_id=asset.id,
                    field_id=due.id,
                    value_json='"2026-10-12"',
                    value_date="2026-10-12",
                    updated_by="member:admin",
                    updated_at=now,
                )
            )
        result: TaskResult = await handler.handle(task)
        assert result.kind is TaskResultKind.DONE
        assert len(sender.sent) == 2
        assert tuple(request.channel for request in sender.sent) == (
            NotificationChannel.EMAIL,
            NotificationChannel.SLACK,
        )
        assert all(request.template_name == "system.data_overdue" for request in sender.sent)
        assert not members.sent
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_overdue_handler_advances_cursor_across_more_than_200_assets() -> None:
    """The keyset cursor attempts every overdue asset once across 200-row batches."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, 12, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    owners: FakeOwners = FakeOwners(None)
    handler: DataOverdueHandler = DataOverdueHandler(
        harness.sessions,
        FakeMemberNotifier(),
        FakeSender(),
        FakeRecipients(),
        owners,
        audit,
        FakeClock(now),
        None,
    )
    task: ClaimedTask = ClaimedTask(
        "task-3", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "due-key-3"
    )
    asset_count: int = 201
    try:
        async with harness.sessions.begin() as session:
            due: DataFieldRow = _field(
                "due-batch", "deletion_due", DataFieldRole.DELETION_DUE, "date"
            )
            session.add(due)
            for number in range(1, asset_count + 1):
                asset: DataAssetRow = _asset(f"asset-{number:03}", number, now)
                session.add(asset)
                session.add(
                    DataFieldValueRow(
                        asset_id=asset.id,
                        field_id=due.id,
                        value_json='"2026-10-12"',
                        value_date="2026-10-12",
                        updated_by="member:admin",
                        updated_at=now,
                    )
                )
        result: TaskResult = await handler.handle(task)
        assert result.kind is TaskResultKind.DONE
        assert owners.calls == asset_count
        assert len(audit.entries) == asset_count
        async with harness.sessions() as session:
            marked: tuple[DataAssetRow, ...] = tuple(
                (await session.scalars(select(DataAssetRow))).all()
            )
            assert len(marked) == asset_count
            assert all(asset.overdue_notified_for == "2026-10-12" for asset in marked)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_overdue_handler_isolates_failed_asset_and_retries_it_next_run() -> None:
    """A failed send rolls back only its asset and the next run retries it."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, 12, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    members: FailingMemberNotifier = FailingMemberNotifier(failed_data_short_id="DATA-1")
    handler: DataOverdueHandler = DataOverdueHandler(
        harness.sessions,
        members,
        FakeSender(),
        FakeRecipients(),
        FakeOwners("member:active"),
        audit,
        FakeClock(now),
        None,
    )
    task: ClaimedTask = ClaimedTask(
        "task-failure", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "failure-key"
    )
    try:
        async with harness.sessions.begin() as session:
            due: DataFieldRow = _field(
                "due-failure", "deletion_due", DataFieldRole.DELETION_DUE, "date"
            )
            session.add(due)
            for number in (1, 2, 3):
                asset: DataAssetRow = _asset(f"asset-{number}", number, now)
                session.add(asset)
                session.add(
                    DataFieldValueRow(
                        asset_id=asset.id,
                        field_id=due.id,
                        value_json='"2026-10-12"',
                        value_date="2026-10-12",
                        updated_by="member:admin",
                        updated_at=now,
                    )
                )
        result: TaskResult = await handler.handle(task)
        assert result.kind is TaskResultKind.DONE
        assert members.attempts == ["DATA-1", "DATA-2", "DATA-3"]
        assert tuple(notification.context.data_short_id for notification in members.sent) == (
            "DATA-2",
            "DATA-3",
        )
        assert len(audit.entries) == 2
        assert {entry.target_id for entry in audit.entries} == {"asset-2", "asset-3"}
        async with harness.sessions() as session:
            assets: tuple[DataAssetRow, ...] = tuple(
                (await session.scalars(select(DataAssetRow).order_by(DataAssetRow.id))).all()
            )
            assert tuple(asset.overdue_notified_for for asset in assets) == (
                None,
                "2026-10-12",
                "2026-10-12",
            )
        result = await handler.handle(task)
        assert result.kind is TaskResultKind.DONE
        assert members.attempts == ["DATA-1", "DATA-2", "DATA-3", "DATA-1"]
        assert len(members.sent) == 2
        assert len(audit.entries) == 2
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_overdue_handler_resends_when_due_date_changes() -> None:
    """A changed deletion due date triggers a new notification and mark."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, 12, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    members: FakeMemberNotifier = FakeMemberNotifier()
    handler: DataOverdueHandler = DataOverdueHandler(
        harness.sessions,
        members,
        FakeSender(),
        FakeRecipients(),
        FakeOwners("member:active"),
        audit,
        FakeClock(now),
        None,
    )
    task: ClaimedTask = ClaimedTask(
        "task-date", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "date-key"
    )
    try:
        async with harness.sessions.begin() as session:
            asset: DataAssetRow = _asset("asset-date", 4, now)
            due: DataFieldRow = _field(
                "due-date", "deletion_due", DataFieldRole.DELETION_DUE, "date"
            )
            session.add_all((asset, due))
            session.add(
                DataFieldValueRow(
                    asset_id=asset.id,
                    field_id=due.id,
                    value_json='"2026-10-12"',
                    value_date="2026-10-12",
                    updated_by="member:admin",
                    updated_at=now,
                )
            )
        assert (await handler.handle(task)).kind is TaskResultKind.DONE
        assert len(members.sent) == 1
        async with harness.sessions.begin() as session:
            due_value: DataFieldValueRow | None = await session.get(
                DataFieldValueRow, ("asset-date", "due-date")
            )
            assert due_value is not None
            due_value.value_json = '"2026-10-11"'
            due_value.value_date = "2026-10-11"
        assert (await handler.handle(task)).kind is TaskResultKind.DONE
        assert len(members.sent) == 2
        async with harness.sessions() as session:
            asset_after: DataAssetRow | None = await session.get(DataAssetRow, "asset-date")
            assert asset_after is not None
            assert asset_after.overdue_notified_for == "2026-10-11"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_overdue_handler_does_not_notify_before_due_date() -> None:
    """A future deletion date leaves the asset and audit unchanged."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, 12, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    members: FakeMemberNotifier = FakeMemberNotifier()
    sender: FakeSender = FakeSender()
    handler: DataOverdueHandler = DataOverdueHandler(
        harness.sessions,
        members,
        sender,
        FakeRecipients(),
        FakeOwners(None),
        audit,
        FakeClock(now),
        None,
    )
    task: ClaimedTask = ClaimedTask(
        "task-future", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "future-key"
    )
    try:
        async with harness.sessions.begin() as session:
            asset: DataAssetRow = _asset("asset-future", 5, now)
            due: DataFieldRow = _field(
                "due-future", "deletion_due", DataFieldRole.DELETION_DUE, "date"
            )
            session.add_all((asset, due))
            session.add(
                DataFieldValueRow(
                    asset_id=asset.id,
                    field_id=due.id,
                    value_json='"2026-10-20"',
                    value_date="2026-10-20",
                    updated_by="member:admin",
                    updated_at=now,
                )
            )
        result: TaskResult = await handler.handle(task)
        assert result.kind is TaskResultKind.DONE
        assert not members.sent
        assert not sender.sent
        assert not audit.entries
        async with harness.sessions() as session:
            asset_after: DataAssetRow | None = await session.get(DataAssetRow, "asset-future")
            assert asset_after is not None
            assert asset_after.overdue_notified_for is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_overdue_handler_excludes_deleted_and_includes_unknown_state() -> None:
    """Deleted assets are excluded while unknown-state assets remain eligible."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, 12, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    members: FakeMemberNotifier = FakeMemberNotifier()
    handler: DataOverdueHandler = DataOverdueHandler(
        harness.sessions,
        members,
        FakeSender(),
        FakeRecipients(),
        FakeOwners("member:active"),
        audit,
        FakeClock(now),
        None,
    )
    task: ClaimedTask = ClaimedTask(
        "task-states", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "state-key"
    )
    try:
        async with harness.sessions.begin() as session:
            due: DataFieldRow = _field(
                "due-states", "deletion_due", DataFieldRole.DELETION_DUE, "date"
            )
            session.add(due)
            deleted: DataAssetRow = _asset("asset-deleted", 6, now)
            deleted.state = DataAssetState.DELETED.value
            unknown: DataAssetRow = _asset("asset-unknown", 7, now)
            unknown.state = DataAssetState.UNKNOWN.value
            session.add_all((deleted, unknown))
            for asset in (deleted, unknown):
                session.add(
                    DataFieldValueRow(
                        asset_id=asset.id,
                        field_id=due.id,
                        value_json='"2026-10-12"',
                        value_date="2026-10-12",
                        updated_by="member:admin",
                        updated_at=now,
                    )
                )
        result: TaskResult = await handler.handle(task)
        assert result.kind is TaskResultKind.DONE
        assert tuple(notification.context.data_short_id for notification in members.sent) == (
            "DATA-7",
        )
        assert len(audit.entries) == 1
        assert audit.entries[0].target_id == "asset-unknown"
        async with harness.sessions() as session:
            deleted_after: DataAssetRow | None = await session.get(DataAssetRow, "asset-deleted")
            unknown_after: DataAssetRow | None = await session.get(DataAssetRow, "asset-unknown")
            assert deleted_after is not None
            assert unknown_after is not None
            assert deleted_after.overdue_notified_for is None
            assert unknown_after.overdue_notified_for == "2026-10-12"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_overdue_handler_marks_and_audits_when_no_recipient_exists() -> None:
    """No recipient still records the due date with zero notifications."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, 12, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    members: FakeMemberNotifier = FakeMemberNotifier()
    sender: FakeSender = FakeSender()
    handler: DataOverdueHandler = DataOverdueHandler(
        harness.sessions,
        members,
        sender,
        FakeRecipients(),
        FakeOwners(None),
        audit,
        FakeClock(now),
        None,
    )
    task: ClaimedTask = ClaimedTask(
        "task-none", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "none-key"
    )
    try:
        async with harness.sessions.begin() as session:
            asset: DataAssetRow = _asset("asset-none", 8, now)
            due: DataFieldRow = _field(
                "due-none", "deletion_due", DataFieldRole.DELETION_DUE, "date"
            )
            session.add_all((asset, due))
            session.add(
                DataFieldValueRow(
                    asset_id=asset.id,
                    field_id=due.id,
                    value_json='"2026-10-12"',
                    value_date="2026-10-12",
                    updated_by="member:admin",
                    updated_at=now,
                )
            )
        result: TaskResult = await handler.handle(task)
        assert result.kind is TaskResultKind.DONE
        assert not members.sent
        assert not sender.sent
        assert len(audit.entries) == 1
        entry: AuditEntry = audit.entries[0]
        assert entry.action == "data_asset.overdue_notified"
        recipient_kind: JsonValue | None = entry.details.get("recipient_kind")
        notification_count: JsonValue | None = entry.details.get("notification_count")
        assert recipient_kind is not None
        assert recipient_kind.as_str() == "none"
        assert notification_count is not None
        assert notification_count.as_int() == 0
        async with harness.sessions() as session:
            asset_after: DataAssetRow | None = await session.get(DataAssetRow, "asset-none")
            assert asset_after is not None
            assert asset_after.overdue_notified_for == "2026-10-12"
    finally:
        await harness.close()
