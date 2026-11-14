"""Field schema mutation rules and audit behavior."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DataFieldRole, DataFieldType, ErrorCode
from telescreen.db.models import DataFieldRow, DataFieldValueRow
from telescreen.inventory.fields import FieldOption, FieldService
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, github_token


@dataclass
class RecordingAudit(AuditLog):
    """Capture safe audit entries for assertions."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one audit entry."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@pytest.mark.asyncio
async def test_field_schema_enforces_immutable_keys_roles_and_exact_reorder() -> None:
    """Role conflicts and incomplete reorder requests fail without schema writes."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    service: FieldService = FieldService(audit, Redactor.default(), FakeClock(now))
    try:
        async with harness.sessions.begin() as session:
            purpose: DataFieldRow = await service.create(
                session,
                "purpose",
                "Purpose",
                None,
                DataFieldType.TEXT,
                (),
                True,
                DataFieldRole.PURPOSE,
                "member:admin",
            )
            with pytest.raises(TelescreenError) as duplicate_role:
                await service.create(
                    session,
                    "purpose_again",
                    "Purpose again",
                    None,
                    DataFieldType.TEXT,
                    (),
                    False,
                    DataFieldRole.PURPOSE,
                    "member:admin",
                )
            assert duplicate_role.value.code is ErrorCode.CONFLICT
            deletion_due: DataFieldRow = await service.create(
                session,
                "deletion_due",
                "Deletion date",
                None,
                DataFieldType.DATE,
                (),
                False,
                DataFieldRole.DELETION_DUE,
                "member:admin",
            )
            with pytest.raises(TelescreenError) as duplicate_due:
                await service.create(
                    session,
                    "deletion_due_again",
                    "Another deletion date",
                    None,
                    DataFieldType.DATE,
                    (),
                    False,
                    DataFieldRole.DELETION_DUE,
                    "member:admin",
                )
            assert duplicate_due.value.code is ErrorCode.CONFLICT
            with pytest.raises(TelescreenError) as bad_order:
                await service.reorder(session, (purpose.id,), "member:admin")
            assert bad_order.value.code is ErrorCode.VALIDATION_ERROR
            ordered: tuple[DataFieldRow, ...] = await service.reorder(
                session, (deletion_due.id, purpose.id), "member:admin"
            )
            assert tuple(row.id for row in ordered if row.archived_at is None) == (
                deletion_due.id,
                purpose.id,
            )
            assert purpose.key == "purpose"
            assert tuple(entry.action for entry in audit.entries) == (
                "data_field.created",
                "data_field.created",
                "data_field.reordered",
            )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_field_type_and_option_changes_are_blocked_when_values_use_them() -> None:
    """Field type changes and option removals preserve existing canonical values."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    service: FieldService = FieldService(audit, Redactor.default(), FakeClock(now))
    option: FieldOption = FieldOption("safe", "Safe")
    second_option: FieldOption = FieldOption("other", "Other")
    try:
        async with harness.sessions.begin() as session:
            field_row: DataFieldRow = await service.create(
                session,
                "category",
                "Category",
                None,
                DataFieldType.SINGLE_SELECT,
                (option, second_option),
                False,
                None,
                "member:admin",
            )
            session.add(
                DataFieldValueRow(
                    asset_id="asset-1",
                    field_id=field_row.id,
                    value_json='"safe"',
                    value_date=None,
                    updated_by="member:admin",
                    updated_at=now,
                )
            )
            await session.flush()
            with pytest.raises(TelescreenError) as type_error:
                await service.update(
                    session,
                    field_row.id,
                    "Category",
                    None,
                    DataFieldType.TEXT,
                    (),
                    False,
                    None,
                    "member:admin",
                )
            assert type_error.value.code is ErrorCode.FIELD_IN_USE
            with pytest.raises(TelescreenError) as option_error:
                await service.update(
                    session,
                    field_row.id,
                    "Category",
                    None,
                    DataFieldType.SINGLE_SELECT,
                    (second_option,),
                    False,
                    None,
                    "member:admin",
                )
            assert option_error.value.code is ErrorCode.FIELD_IN_USE
            with pytest.raises(TelescreenError) as delete_error:
                await service.delete(session, field_row.id, "member:admin")
            assert delete_error.value.code is ErrorCode.FIELD_IN_USE
            empty: DataFieldRow = await service.create(
                session,
                "empty",
                "Empty",
                None,
                DataFieldType.TEXT,
                (),
                False,
                None,
                "member:admin",
            )
            changed: DataFieldRow = await service.update(
                session,
                empty.id,
                "Empty",
                None,
                DataFieldType.INTEGER,
                (),
                False,
                None,
                "member:admin",
            )
            assert changed.field_type == DataFieldType.INTEGER.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_archive_excludes_required_role_and_key_uniqueness_includes_archived() -> None:
    """Archived fields leave required and role sets but retain their immutable keys."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    service: FieldService = FieldService(RecordingAudit(), Redactor.default(), FakeClock(now))
    try:
        async with harness.sessions.begin() as session:
            archived: DataFieldRow = await service.create(
                session,
                "purpose",
                "Purpose",
                None,
                DataFieldType.TEXT,
                (),
                True,
                DataFieldRole.PURPOSE,
                "member:admin",
            )
            await service.archive(session, archived.id, "member:admin")
            active: DataFieldRow = await service.create(
                session,
                "purpose_new",
                "Purpose new",
                None,
                DataFieldType.TEXT,
                (),
                False,
                DataFieldRole.PURPOSE,
                "member:admin",
            )
            with pytest.raises(TelescreenError) as duplicate_key:
                await service.create(
                    session,
                    "purpose",
                    "Purpose duplicate",
                    None,
                    DataFieldType.TEXT,
                    (),
                    False,
                    None,
                    "member:admin",
                )
            assert duplicate_key.value.code is ErrorCode.CONFLICT
            with pytest.raises(TelescreenError) as role_conflict:
                await service.unarchive(session, archived.id, "member:admin")
            assert role_conflict.value.code is ErrorCode.CONFLICT
            with pytest.raises(TelescreenError) as missing_options:
                await service.create(
                    session,
                    "choice",
                    "Choice",
                    None,
                    DataFieldType.SINGLE_SELECT,
                    (),
                    False,
                    None,
                    "member:admin",
                )
            assert missing_options.value.code is ErrorCode.VALIDATION_ERROR
            with pytest.raises(TelescreenError) as bad_role:
                await service.create(
                    session,
                    "bad_owner",
                    "Bad owner",
                    None,
                    DataFieldType.TEXT,
                    (),
                    False,
                    DataFieldRole.OWNER,
                    "member:admin",
                )
            assert bad_role.value.code is ErrorCode.VALIDATION_ERROR
            active_required: tuple[str, ...] = tuple(
                row.key
                for row in await service.list(session)
                if row.required and row.archived_at is None
            )
            assert active_required == ()
            assert active.role == DataFieldRole.PURPOSE.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_field_label_sensitive_value_is_rejected_without_echo_or_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A detected secret in a label is rejected using static text only."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    service: FieldService = FieldService(RecordingAudit(), Redactor.default(), FakeClock(now))
    sample: Sample = github_token()
    try:
        async with harness.sessions.begin() as session:
            with pytest.raises(TelescreenError) as rejected:
                await service.create(
                    session,
                    "label_secret",
                    sample.value,
                    None,
                    DataFieldType.TEXT,
                    (),
                    False,
                    None,
                    "member:admin",
                )
            assert rejected.value.code is ErrorCode.RAW_VALUE_DETECTED
            assert sample.value not in rejected.value.detail
            assert sample.value not in caplog.text
    finally:
        await harness.close()
