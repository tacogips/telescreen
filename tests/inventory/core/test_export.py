"""Offline CSV export contract tests."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.inventory.export import AssetExporter, CsvExport
from telescreen.inventory.queries import AssetFilters, AssetPage, AssetQueries, AssetView
from telescreen.masking.redact import Redactor
from telescreen.ports.directory import ActorDirectory, ActorDisplay
from telescreen.ports.issues import ResourceIssueReader, ResourceIssueRef
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class PagedQueries(AssetQueries):
    """Supply deterministic pages without seeding thousands of database rows."""

    async def list(self, session: AsyncSession, filters: AssetFilters, today: date) -> AssetPage:
        """Return at most 200 synthetic assets and a continuation cursor."""
        del session, today
        page_number: int = int(filters.cursor or "0")
        first_number: int = page_number * 200
        last_number: int = min(first_number + 200, 10001)
        at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
        items: tuple[AssetView, ...] = tuple(
            AssetView(
                id=f"asset-{number}",
                short_id=f"DATA-{number + 1}",
                number=number + 1,
                name=f"Asset {number + 1}",
                source="custom",
                display_locator=f"locator-{number + 1}",
                kind="other",
                origin="registered",
                state="present",
                item_count=None,
                item_trend=(),
                open_issue_count=0,
                owner=None,
                deletion_due=None,
                overdue=False,
                incomplete=False,
                first_seen_at=at,
                last_seen_at=at,
                deleted_at=None,
                collector_config_id=None,
            )
            for number in range(first_number, last_number)
        )
        next_cursor: str | None = str(page_number + 1) if last_number < 10001 else None
        return AssetPage(items, next_cursor)


@dataclass(frozen=True)
class FastExporter(AssetExporter):
    """Skip metadata lookups because this test has no field columns."""

    async def _field_values(self, session: AsyncSession, asset: AssetView) -> tuple[str, ...]:
        """Return no field values for synthetic assets."""
        del session, asset
        return ()


@pytest.mark.asyncio
async def test_export_truncates_after_ten_thousand_rows() -> None:
    """CSV export caps data rows and signals when a later page exists."""
    harness: SqliteHarness = await make_sqlite_harness()
    empty_actors: ActorDirectory
    empty_issues: ResourceIssueReader
    queries: PagedQueries
    try:
        # The synthetic query never uses these ports; satisfy the typed base contract.
        empty_actors = _UnusedActors()
        empty_issues = _UnusedIssues()
        queries = PagedQueries(empty_issues, empty_actors)
        exporter: FastExporter = FastExporter(queries, Redactor.default())
        async with harness.sessions.begin() as session:
            result: CsvExport = await exporter.export(session, AssetFilters(), date(2026, 10, 13))
        assert result.row_count == 10000
        assert result.truncated is True
        assert result.text.count("\r\n") == 10001
    finally:
        await harness.close()


class _UnusedActors(ActorDirectory):
    """Port placeholder that fails if the fake query unexpectedly uses enrichment."""

    async def display(
        self, session: AsyncSession, refs: tuple[str, ...]
    ) -> tuple[ActorDisplay, ...]:
        """Reject unexpected actor resolution in the synthetic query."""
        del session, refs
        raise AssertionError("synthetic pages do not resolve actors")


class _UnusedIssues(ResourceIssueReader):
    """Port placeholder that fails if the fake query unexpectedly reads issues."""

    async def open_issues_for_resources(
        self, session: AsyncSession, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Reject unexpected issue lookup in the synthetic query."""
        del session, resource_keys
        raise AssertionError("synthetic pages do not read issues")
