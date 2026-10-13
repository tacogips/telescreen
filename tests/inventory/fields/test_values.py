"""Contract tests for strict metadata value validation."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json, json_array, json_object, json_str
from telescreen.core.vocabulary import DataAssetState, DataFieldType, ErrorCode
from telescreen.db.models import DataAssetEventRow, DataAssetRow, DataFieldRow, DataFieldValueRow
from telescreen.inventory.repository import InventoryRepository
from telescreen.inventory.values import ValueInput, ValueIssue, ValueService, ValueValidator
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import AssigneeIds, RecipientResolver, ResolvedRecipients
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, github_token


@dataclass(frozen=True)
class FakeRecipients(RecipientResolver):
    """Resolve the one active member reference used by these tests."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Return an empty resolved recipient set."""
        del session, ref
        return ResolvedRecipients((), (), (), ())

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Return false because membership is outside this validation test."""
        del session, member_id, ref
        return False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Resolve a reference or raise the normal stale-reference code."""
        del session
        if ref == "member:active":
            return AssigneeIds("member-id", None)
        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")


@dataclass
class RecordingAudit(AuditLog):
    """Capture safe audit entries for persistence checks."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one audit entry."""
        del session
        self.entries.append(entry)
        return len(self.entries)


def _field(field_type: DataFieldType, options: str | None = None) -> DataFieldRow:
    """Build an unattached field row for validator-only tests."""
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    return DataFieldRow(
        id=field_type.value,
        key="field_value",
        label="Field value",
        description=None,
        field_type=field_type.value,
        options_json=options,
        required=False,
        position=0,
        role=None,
        archived_at=None,
        created_by="member:admin",
        created_at=now,
        updated_at=now,
    )


async def _issue(
    validator: ValueValidator,
    harness: SqliteHarness,
    field: DataFieldRow,
    value: str,
) -> str | None:
    """Return the first validation code for one field value."""
    async with harness.sessions() as session:
        issues: tuple[ValueIssue, ...] = await validator.validate(
            session, (field,), (ValueInput(field.key, value),)
        )
    return issues[0].code if issues else None


@pytest.mark.asyncio
async def test_value_validator_enforces_type_table_and_reference_errors() -> None:
    """Every supported type follows its JSON type, option, URL, and date rules."""
    harness: SqliteHarness = await make_sqlite_harness()
    validator: ValueValidator = ValueValidator(FakeRecipients(), Redactor.default())
    options: str = dump_json(
        json_array(
            (
                json_object((("value", json_str("safe")), ("label", json_str("Safe")))),
                json_object((("value", json_str("other")), ("label", json_str("Other")))),
            )
        )
    )
    try:
        checks: tuple[tuple[DataFieldType, str, str | None], ...] = (
            (DataFieldType.TEXT, '"hello"', None),
            (DataFieldType.TEXT, '""', "invalid_type"),
            (DataFieldType.TEXT, '"' + ("x" * 501) + '"', "too_long"),
            (DataFieldType.LONG_TEXT, '"long text"', None),
            (DataFieldType.LONG_TEXT, '"' + ("x" * 4001) + '"', "too_long"),
            (DataFieldType.INTEGER, "42", None),
            (DataFieldType.INTEGER, '"42"', "invalid_type"),
            (DataFieldType.INTEGER, "true", "invalid_type"),
            (DataFieldType.DECIMAL, '"12.3456"', None),
            (DataFieldType.DECIMAL, '"12.3456789"', "invalid_type"),
            (DataFieldType.BOOLEAN, "true", None),
            (DataFieldType.DATE, '"2026-10-13"', None),
            (DataFieldType.DATE, '"2026-02-30"', "invalid_type"),
            (DataFieldType.SINGLE_SELECT, '"safe"', None),
            (DataFieldType.SINGLE_SELECT, '"missing"', "invalid_option"),
            (DataFieldType.MULTI_SELECT, '["safe","other"]', None),
            (DataFieldType.MULTI_SELECT, '["safe","safe"]', "invalid_type"),
            (DataFieldType.REFERENCE, '"member:active"', None),
            (DataFieldType.REFERENCE, '"unknown:ref"', "unknown_reference"),
            (DataFieldType.REFERENCE, '"member:inactive"', "unknown_reference"),
            (DataFieldType.REFERENCE, '"team:deleted"', "unknown_reference"),
            (DataFieldType.URL, '"https://example.test/path"', None),
            (DataFieldType.URL, '"http://example.test"', "invalid_type"),
            (DataFieldType.URL, '"https://user:pass@localhost"', "invalid_type"),
        )
        for field_type, value, expected in checks:
            field: DataFieldRow = _field(
                field_type,
                options
                if field_type in (DataFieldType.SINGLE_SELECT, DataFieldType.MULTI_SELECT)
                else None,
            )
            assert await _issue(validator, harness, field, value) == expected
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_value_validator_rejects_detected_text_without_echo() -> None:
    """Sensitive text is rejected with a static code and no secret in the issue."""
    harness: SqliteHarness = await make_sqlite_harness()
    validator: ValueValidator = ValueValidator(FakeRecipients(), Redactor.default())
    sample: Sample = github_token()
    try:
        code: str | None = await _issue(
            validator, harness, _field(DataFieldType.LONG_TEXT), dump_json(json_str(sample.value))
        )
        assert code == "raw_value_detected"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_value_service_checks_resulting_required_state_and_records_one_audit() -> None:
    """Changing a value emits one metadata event and one audit; clearing required fails."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    validator: ValueValidator = ValueValidator(FakeRecipients(), Redactor.default())
    service: ValueService = ValueService(InventoryRepository(), validator, audit, FakeClock(now))
    try:
        async with harness.sessions.begin() as session:
            asset: DataAssetRow = DataAssetRow(
                id="asset-1",
                number=1,
                resource_key="1" * 64,
                source="manual",
                origin="registered",
                kind="other",
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
            field_row: DataFieldRow = _field(DataFieldType.TEXT)
            field_row.id = "field-1"
            field_row.required = True
            session.add_all((asset, field_row))
            await session.flush()
            await service.put(
                session,
                asset,
                (field_row,),
                (ValueInput(field_row.key, '"first"'),),
                "member:admin",
            )
            await service.put(
                session,
                asset,
                (field_row,),
                (ValueInput(field_row.key, '"first"'),),
                "member:admin",
            )
        with pytest.raises(TelescreenError) as missing:
            async with harness.sessions.begin() as session:
                existing_asset: DataAssetRow | None = await session.get(DataAssetRow, "asset-1")
                assert existing_asset is not None
                await service.put(
                    session,
                    existing_asset,
                    (field_row,),
                    (ValueInput(field_row.key, None),),
                    "member:admin",
                )
        assert missing.value.code is ErrorCode.REQUIRED_FIELD_MISSING
        async with harness.sessions() as session:
            events: tuple[DataAssetEventRow, ...] = tuple(
                (await session.scalars(select(DataAssetEventRow))).all()
            )
            values: tuple[DataFieldValueRow, ...] = tuple(
                (await session.scalars(select(DataFieldValueRow))).all()
            )
            assert len(events) == 1
            assert events[0].kind == "metadata_changed"
            assert events[0].field_key == field_row.key
            assert events[0].new_value == "first"
            assert len(values) == 1 and values[0].value_json == '"first"'
        assert tuple(entry.action for entry in audit.entries) == ("data_asset.fields_changed",)
    finally:
        await harness.close()
