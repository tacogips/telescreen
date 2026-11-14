"""Read-path enrichment tests for inventory queries and CSV export."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from telescreen.core.vocabulary import ActorDisplayKind
from telescreen.db.models import DataAssetRow, DataFieldRow, DataFieldValueRow
from telescreen.inventory.export import AssetExporter, CsvExport
from telescreen.inventory.queries import AssetFilters, AssetPage, AssetQueries
from telescreen.masking.redact import Redactor
from telescreen.ports.asset_context import AssetIssueLookup
from telescreen.ports.identity_context import ActorDisplay, ActorLookup
from telescreen.ports.issues import ResourceIssueRef
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class UnknownActorLookup(ActorLookup):
    """Return Unknown for stale references and record page batch calls."""

    calls: int = 0

    async def display(self, refs: tuple[str, ...]) -> tuple[ActorDisplay, ...]:
        """Return stale refs without failing the inventory read."""
        self.calls += 1
        return tuple(
            ActorDisplay(ref, ActorDisplayKind.UNKNOWN, "Unknown", None, False) for ref in refs
        )


@dataclass(frozen=True)
class EmptyIssues(AssetIssueLookup):
    """Return no issue links for inventory query tests."""

    async def open_issues_for_resources(
        self, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Return an empty page of issues."""
        del resource_keys
        return ()


@pytest.mark.asyncio
async def test_stale_owner_is_batched_and_exportable() -> None:
    """Stale owner refs remain visible and do not fail list or export reads."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    actors: UnknownActorLookup = UnknownActorLookup()
    queries: AssetQueries = AssetQueries(EmptyIssues(), actors)
    try:
        async with harness.sessions.begin() as session:
            asset: DataAssetRow = DataAssetRow(
                id="asset-id",
                number=1,
                resource_key="a" * 64,
                source="custom",
                origin="discovered",
                kind="other",
                state="present",
                name="Report",
                display_locator="safe-locator",
                collector_config_id=None,
                sweep_covered=False,
                item_count=None,
                first_seen_at=now,
                last_seen_at=now,
                deleted_at=None,
                overdue_notified_for=None,
                created_by=None,
                created_at=now,
                updated_at=now,
            )
            field: DataFieldRow = DataFieldRow(
                id="field-id",
                key="owner",
                label="Owner",
                description=None,
                field_type="reference",
                options_json=None,
                required=False,
                position=0,
                role="owner",
                archived_at=None,
                created_by="member:admin",
                created_at=now,
                updated_at=now,
            )
            session.add_all((asset, field))
            await session.flush()
            session.add(
                DataFieldValueRow(
                    asset_id=asset.id,
                    field_id=field.id,
                    value_json='"team:retired"',
                    value_date=None,
                    updated_by="member:admin",
                    updated_at=now,
                )
            )
            await session.flush()
            page: AssetPage = await queries.list(
                session, AssetFilters(owner="team:retired"), date(2026, 10, 13)
            )
            assert len(page.items) == 1
            assert page.items[0].owner is not None
            assert page.items[0].owner.ref == "team:retired"
            assert page.items[0].owner.display_name == "Unknown"
            assert actors.calls == 1

            exporter: AssetExporter = AssetExporter(queries, Redactor.default())
            csv_result: CsvExport = await exporter.export(
                session, AssetFilters(owner="team:retired"), date(2026, 10, 13)
            )
            assert csv_result.row_count == 1
            assert "Unknown" in csv_result.text
            assert actors.calls == 2
    finally:
        await harness.close()
