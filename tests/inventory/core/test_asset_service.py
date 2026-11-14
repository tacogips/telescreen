"""Offline tests for audited member changes to data assets."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DataAssetState, ErrorCode
from telescreen.db.models import DataAssetEventRow, DataAssetRow
from telescreen.inventory.assets import AssetService
from telescreen.inventory.repository import InventoryRepository
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, github_token


@dataclass
class RecordingAudit(AuditLog):
    """Keep audit entries in memory for write assertions."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one already-safe audit entry."""
        del session
        self.entries.append(entry)
        return len(self.entries)


def _asset(asset_id: str, number: int, origin: str, at: datetime) -> DataAssetRow:
    """Build one minimal asset row for service operations."""
    return DataAssetRow(
        id=asset_id,
        number=number,
        resource_key=f"{number:064x}",
        source="custom",
        origin=origin,
        kind="other",
        state=DataAssetState.PRESENT.value,
        name=asset_id,
        display_locator=asset_id,
        collector_config_id=None,
        sweep_covered=False,
        item_count=5,
        first_seen_at=at,
        last_seen_at=at,
        deleted_at=None,
        overdue_notified_for=None,
        created_by="member:admin",
        created_at=at,
        updated_at=at,
    )


@pytest.mark.asyncio
async def test_asset_service_validates_writes_and_appends_audits() -> None:
    """Manual mutations validate inputs, update only registered counts, and audit writes."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    audit: RecordingAudit = RecordingAudit()
    service: AssetService = AssetService(
        InventoryRepository(), audit, Redactor.default(), FakeClock(now)
    )
    sample: Sample = github_token()
    try:
        async with harness.sessions.begin() as session:
            discovered: DataAssetRow = _asset("discovered", 1, "discovered", now)
            registered: DataAssetRow = _asset("registered", 2, "registered", now)
            session.add_all((discovered, registered))
            await session.flush()

            with pytest.raises(TelescreenError) as name_error:
                await service.patch(session, discovered.id, "Renamed", None, "member:editor")
            assert name_error.value.code is ErrorCode.VALIDATION_ERROR

            await service.record_event(
                session,
                registered.id,
                now,
                "member:editor",
                items_added=2,
                items_removed=1,
                item_count_after=None,
                note="Reviewed inventory",
            )
            await service.record_event(
                session,
                discovered.id,
                now,
                "member:editor",
                items_added=2,
                items_removed=1,
                item_count_after=9,
                note=None,
            )
            assert registered.item_count == 6
            assert discovered.item_count == 5

            with pytest.raises(TelescreenError) as future_error:
                await service.record_event(
                    session,
                    registered.id,
                    now + timedelta(minutes=6),
                    "member:editor",
                    None,
                    None,
                    None,
                    None,
                )
            assert future_error.value.code is ErrorCode.VALIDATION_ERROR

            with pytest.raises(TelescreenError) as secret_error:
                await service.record_event(
                    session,
                    registered.id,
                    now,
                    "member:editor",
                    None,
                    None,
                    None,
                    sample.value,
                )
            assert secret_error.value.code is ErrorCode.RAW_VALUE_DETECTED
            assert sample.value not in secret_error.value.detail

            with pytest.raises(TelescreenError) as restore_error:
                await service.restore(session, registered.id, "member:editor")
            assert restore_error.value.code is ErrorCode.CONFLICT

            await service.mark_deleted(
                session, registered.id, "member:editor", None, "Validated removal"
            )
            with pytest.raises(TelescreenError) as deleted_error:
                await service.mark_deleted(session, registered.id, "member:editor", None, None)
            assert deleted_error.value.code is ErrorCode.CONFLICT
            await service.restore(session, registered.id, "member:editor")
            assert registered.state == DataAssetState.PRESENT.value

            rows: tuple[DataAssetEventRow, ...] = tuple(
                (await session.scalars(select(DataAssetEventRow))).all()
            )
            assert tuple(row.kind for row in rows) == (
                "manual_change",
                "manual_change",
                "deleted",
                "restored",
            )
        assert tuple(entry.action for entry in audit.entries) == (
            "data_asset.event_recorded",
            "data_asset.event_recorded",
            "data_asset.marked_deleted",
            "data_asset.restored",
        )
    finally:
        await harness.close()
