"""Placement policy storage, audit and queue behavior tests."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import EngineTaskKind, ErrorCode, SourceFormat
from telescreen.db.models import PlacementPolicyRow
from telescreen.issues.policy.loader import PolicyValidationError
from telescreen.issues.policy.model import PolicyVersionView
from telescreen.issues.policy.store import PolicyStore
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients
from telescreen.ports.workflow import TaskSpec
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import github_token


@dataclass
class RecordingAudit:
    """Capture static audit entries and provide stable audit sequences."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class RecordingQueue:
    """Capture policy re-evaluation tasks."""

    tasks: list[TaskSpec] = field(default_factory=list)

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        del session
        self.tasks.append(spec)
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        del session, ref_id
        return 0


@dataclass
class FakeResolver:
    """Resolve the configured team or return the standard unknown-recipient error."""

    unknown: bool = False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        del session
        if self.unknown:
            raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")
        assert ref.startswith("team:")
        return AssigneeIds(None, "team-id")

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        del session, ref
        return ResolvedRecipients((), (), (), ())

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        del session, member_id, ref
        return False


def make_store(
    audit: RecordingAudit | None = None,
    queue: RecordingQueue | None = None,
    resolver: FakeResolver | None = None,
) -> tuple[PolicyStore, RecordingAudit, RecordingQueue]:
    """Build a policy store with in-memory recording ports."""
    actual_audit: RecordingAudit = audit or RecordingAudit()
    actual_queue: RecordingQueue = queue or RecordingQueue()
    store: PolicyStore = PolicyStore(
        Redactor.default(),
        Redactor.default().detector,
        actual_audit,
        actual_queue,
        resolver or FakeResolver(),
        FakeClock(datetime(2026, 10, 10, tzinfo=UTC)),
    )
    return store, actual_audit, actual_queue


@pytest.mark.asyncio
async def test_create_versions_activate_and_disable_enqueue_after_audit() -> None:
    """Create inactive versions and enqueue reevaluation for active-state changes."""
    harness: SqliteHarness = await make_sqlite_harness()
    dependencies: tuple[PolicyStore, RecordingAudit, RecordingQueue] = make_store()
    store: PolicyStore
    audit: RecordingAudit
    queue: RecordingQueue
    store, audit, queue = dependencies
    try:
        async with harness.sessions.begin() as session:
            first: PolicyVersionView = await store.create(
                session,
                '{"name":"policy","effect":"violate"}',
                SourceFormat.JSON,
                "member:operator",
            )
            second: PolicyVersionView = await store.create(
                session, '{"name":"policy","effect":"allow"}', SourceFormat.JSON, "member:operator"
            )
            assert (first.version, first.active, first.enabled) == (1, False, True)
            assert (second.version, second.active, second.enabled) == (2, False, True)
            activated: PolicyVersionView = await store.activate(
                session, "policy", 2, "member:operator"
            )
            assert activated.active is True
            listed: tuple[PolicyVersionView, ...] = await store.list(session)
            versions: tuple[PolicyVersionView, ...] = await store.versions(session, "policy")
            current: PolicyVersionView = await store.version(session, "policy", 2)
            assert tuple(item.version for item in listed) == (2,)
            assert tuple(item.version for item in versions) == (1, 2)
            assert current.active is True
            await store.disable(session, "policy", "member:operator")
        assert [entry.action for entry in audit.entries] == [
            "policy.created",
            "policy.created",
            "policy.activated",
            "policy.disabled",
        ]
        assert [task.kind for task in queue.tasks] == [
            EngineTaskKind.POLICY_REEVALUATE,
            EngineTaskKind.POLICY_REEVALUATE,
        ]
        assert queue.tasks[0].idempotency_key == "policy:policy:2:activated:3"
        assert queue.tasks[1].idempotency_key == "policy:policy:2:disabled:4"
        async with harness.sessions() as session:
            rows: tuple[PlacementPolicyRow, ...] = tuple(
                (await session.scalars(select(PlacementPolicyRow))).all()
            )
        assert all(not row.active and not row.enabled for row in rows)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_raw_detected_source_is_rejected_before_persistence() -> None:
    """Reject detected values with the stable 422 error and no database rows."""
    harness: SqliteHarness = await make_sqlite_harness()
    dependencies: tuple[PolicyStore, RecordingAudit, RecordingQueue] = make_store()
    store: PolicyStore = dependencies[0]
    secret: str = github_token().value
    source: str = f'{{"name":"safe","effect":"violate","rationale":"{secret}"}}'
    try:
        async with harness.sessions.begin() as session:
            with pytest.raises(PolicyValidationError) as caught:
                await store.create(session, source, SourceFormat.JSON, "member:operator")
            assert caught.value.code is ErrorCode.RAW_VALUE_DETECTED
            assert caught.value.issues[0].code == "raw_value_detected"
            assert secret not in str(caught.value)
        async with harness.sessions() as session:
            assert await session.scalar(select(PlacementPolicyRow.id)) is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_long_policy_name_uses_version_id_as_task_reference() -> None:
    """Keep valid 64-character policy names within the durable task reference limit."""
    harness: SqliteHarness = await make_sqlite_harness()
    dependencies: tuple[PolicyStore, RecordingAudit, RecordingQueue] = make_store()
    store: PolicyStore = dependencies[0]
    queue: RecordingQueue = dependencies[2]
    name: str = "p" + "x" * 63
    try:
        async with harness.sessions.begin() as session:
            await store.create(
                session,
                f'{{"name":"{name}","effect":"violate"}}',
                SourceFormat.JSON,
                "member:operator",
            )
            await store.activate(session, name, 1, "member:operator")
            policy_id: str | None = await session.scalar(
                select(PlacementPolicyRow.id).where(PlacementPolicyRow.name == name)
            )
            await store.disable(session, name, "member:operator")
        assert policy_id is not None
        assert len(queue.tasks) == 2
        assert queue.tasks[0].ref_id == policy_id
        assert len(queue.tasks[0].ref_id) <= 36
        task_name: JsonValue | None = queue.tasks[0].payload.get("name")
        assert task_name is not None
        assert task_name.as_str() == name
        assert queue.tasks[1].ref_id == policy_id
        disabled_task_name: JsonValue | None = queue.tasks[1].payload.get("name")
        assert disabled_task_name is not None
        assert disabled_task_name.as_str() == name
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_unknown_owner_team_is_a_path_only_validation_issue() -> None:
    """Reject a non-existent team without returning the submitted team slug."""
    harness: SqliteHarness = await make_sqlite_harness()
    dependencies: tuple[PolicyStore, RecordingAudit, RecordingQueue] = make_store(
        resolver=FakeResolver(unknown=True)
    )
    store: PolicyStore = dependencies[0]
    try:
        async with harness.sessions.begin() as session:
            with pytest.raises(PolicyValidationError) as caught:
                await store.create(
                    session,
                    '{"name":"safe","effect":"violate","owner_team":"unknown-team"}',
                    SourceFormat.JSON,
                    "member:operator",
                )
            assert [(issue.path, issue.code) for issue in caught.value.issues] == [
                ("/owner_team", "unknown_team")
            ]
            assert "unknown-team" not in str(caught.value)
    finally:
        await harness.close()
