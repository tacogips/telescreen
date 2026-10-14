"""Manual registration uniqueness, lifecycle and audit tests."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DataAssetKind, DataFieldType, ErrorCode
from telescreen.db.models import (
    DataAssetEventRow,
    DataAssetRow,
    DataFieldRow,
    DataFieldValueRow,
    InventoryCounterRow,
)
from telescreen.inventory.registration import RegistrationBody, RegistrationService
from telescreen.inventory.repository import InventoryRepository
from telescreen.inventory.values import ValueInput, ValueService, ValueValidator
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, github_token


@dataclass(frozen=True)
class RaceRepository(InventoryRepository):
    """Model a concurrent insert that wins after the uniqueness precheck."""

    async def get_by_resource_key(
        self, session: AsyncSession, resource_key: str, lock: bool = False
    ) -> DataAssetRow | None:
        """Hide the winner from precheck so the database unique key resolves the race."""
        del session, resource_key, lock
        return None


@dataclass(frozen=True)
class EmptyRecipients:
    """No recipient values are needed for these registration cases."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Return no delivery targets."""
        del session, ref
        return ResolvedRecipients((), (), (), ())

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Return false for unused membership checks."""
        del session, member_id, ref
        return False

    async def assignee_of(self, ref: str) -> AssigneeIds:
        """Reject unused references as unknown."""
        del ref
        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")


@dataclass
class RecordingAudit(AuditLog):
    """Capture safe audit records."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one audit entry."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@pytest.mark.asyncio
async def test_registration_creates_asset_event_audit_and_conflicts_on_duplicate() -> None:
    """Registration is discoverable, audited, and unique by registered source and locator."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    redactor: Redactor = Redactor.default()
    repository: InventoryRepository = InventoryRepository()
    validator: ValueValidator = ValueValidator(EmptyRecipients(), redactor)
    values: ValueService = ValueService(repository, validator, audit, FakeClock(now))
    service: RegistrationService = RegistrationService(
        repository, values, Fingerprinter(b"f" * 32, "test"), redactor, audit, FakeClock(now)
    )
    body: RegistrationBody = RegistrationBody(
        "Quarterly export",
        "manual",
        "https://example.test/export",
        DataAssetKind.FILE,
        4,
        (ValueInput("purpose", '"Finance reporting"'),),
    )
    try:
        required: DataFieldRow = DataFieldRow(
            id="field-purpose",
            key="purpose",
            label="Purpose",
            description=None,
            field_type=DataFieldType.TEXT.value,
            options_json=None,
            required=True,
            position=0,
            role=None,
            archived_at=None,
            created_by="member:admin",
            created_at=now,
            updated_at=now,
        )
        async with harness.sessions.begin() as session:
            session.add(required)
        with pytest.raises(TelescreenError) as missing_required:
            async with harness.sessions.begin() as session:
                await service.register(
                    session,
                    RegistrationBody(
                        "Missing purpose",
                        "manual",
                        "https://example.test/missing",
                        DataAssetKind.FILE,
                    ),
                    "member:admin",
                )
        assert missing_required.value.code is ErrorCode.REQUIRED_FIELD_MISSING
        sample: Sample = github_token()
        with pytest.raises(TelescreenError) as raw_name:
            async with harness.sessions.begin() as session:
                await service.register(
                    session,
                    RegistrationBody(
                        sample.value, "manual", "https://example.test/secret", DataAssetKind.FILE
                    ),
                    "member:admin",
                )
        assert raw_name.value.code is ErrorCode.RAW_VALUE_DETECTED
        assert sample.value not in raw_name.value.detail
        async with harness.sessions.begin() as session:
            asset_id: str = await service.register(session, body, "member:admin")
            asset: DataAssetRow | None = await session.get(DataAssetRow, asset_id)
            assert asset is not None
            assert asset.number == 1
            assert asset.origin == "registered"
            assert asset.item_count == 4
            with pytest.raises(TelescreenError) as duplicate:
                await service.register(session, body, "member:admin")
            assert duplicate.value.code is ErrorCode.CONFLICT
            events: tuple[DataAssetEventRow, ...] = tuple(
                (await session.scalars(select(DataAssetEventRow))).all()
            )
            assert tuple(sorted(event.kind for event in events)) == (
                "metadata_changed",
                "registered",
            )
            registered: DataAssetEventRow = next(
                event for event in events if event.kind == "registered"
            )
            assert registered.item_count_after == 4
            metadata: DataFieldValueRow | None = await session.get(
                DataFieldValueRow, (asset.id, "field-purpose")
            )
            assert metadata is not None and metadata.value_json == '"Finance reporting"'
        assert tuple(entry.action for entry in audit.entries) == (
            "data_asset.fields_changed",
            "data_asset.registered",
        )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_registration_maps_insert_race_to_conflict() -> None:
    """A concurrent resource-key winner is reported as a conflict, not an error."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    redactor: Redactor = Redactor.default()
    fingerprinter: Fingerprinter = Fingerprinter(b"f" * 32, "test")
    repository: RaceRepository = RaceRepository()
    validator: ValueValidator = ValueValidator(EmptyRecipients(), redactor)
    values: ValueService = ValueService(repository, validator, audit, FakeClock(now))
    service: RegistrationService = RegistrationService(
        repository, values, fingerprinter, redactor, audit, FakeClock(now)
    )
    body: RegistrationBody = RegistrationBody(
        "Quarterly export", "manual", "https://example.test/export", DataAssetKind.FILE
    )
    resource_key: str = fingerprinter.resource_key("registered:manual", body.locator)
    try:
        async with harness.sessions.begin() as session:
            session.add_all(
                (
                    InventoryCounterRow(name="data_asset_number", value=1),
                    DataAssetRow(
                        id="race-winner",
                        number=1,
                        resource_key=resource_key,
                        source="manual",
                        origin="registered",
                        kind=DataAssetKind.FILE.value,
                        state="present",
                        name="Concurrent winner",
                        display_locator=body.locator,
                        collector_config_id=None,
                        sweep_covered=False,
                        item_count=None,
                        first_seen_at=now,
                        last_seen_at=now,
                        deleted_at=None,
                        overdue_notified_for=None,
                        created_by="member:other",
                        created_at=now,
                        updated_at=now,
                    ),
                )
            )
        with pytest.raises(TelescreenError) as conflict:
            async with harness.sessions.begin() as session:
                await service.register(session, body, "member:admin")
        assert conflict.value.code is ErrorCode.CONFLICT
    finally:
        await harness.close()
