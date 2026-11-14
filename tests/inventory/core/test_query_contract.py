"""Offline tests for inventory query predicates, paging, trends, and detail."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.sql.elements import ColumnElement

from telescreen.core.json import dump_json, json_array, json_str
from telescreen.core.vocabulary import ActorDisplayKind
from telescreen.db.models import DataAssetEventRow, DataAssetRow, DataFieldRow, DataFieldValueRow
from telescreen.inventory.export import AssetExporter, CsvExport
from telescreen.inventory.queries import (
    AssetDetailView,
    AssetFilters,
    AssetPage,
    AssetQueries,
    CountsView,
    FieldFilter,
    SeriesPoint,
    _cursor_datetime,
    _sort_column,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.asset_context import AssetIssueLookup
from telescreen.ports.identity_context import ActorDisplay, ActorLookup
from telescreen.ports.issues import ResourceIssueRef
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class RecordingActors(ActorLookup):
    """Return stale-safe display names while recording each page batch."""

    calls: list[tuple[str, ...]]

    async def display(self, refs: tuple[str, ...]) -> tuple[ActorDisplay, ...]:
        """Resolve all supplied refs to the safe unknown display."""
        self.calls.append(refs)
        return tuple(
            ActorDisplay(ref, ActorDisplayKind.UNKNOWN, "Unknown", None, False) for ref in refs
        )


@dataclass(frozen=True)
class EmptyIssues(AssetIssueLookup):
    """Return no issue references for query contract tests."""

    async def open_issues_for_resources(
        self, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Return an empty result without external access."""
        del resource_keys
        return ()


def _asset(
    asset_id: str,
    number: int,
    name: str,
    at: datetime,
    state: str = "present",
) -> DataAssetRow:
    """Build one row with stable ordering and resource keys."""
    return DataAssetRow(
        id=asset_id,
        number=number,
        resource_key=f"{number:064x}",
        source="custom",
        origin="discovered",
        kind="table",
        state=state,
        name=name,
        display_locator=f"locator-{name.lower()}",
        collector_config_id="config-id",
        sweep_covered=True,
        item_count=4,
        first_seen_at=at,
        last_seen_at=at,
        deleted_at=at if state == "deleted" else None,
        overdue_notified_for=None,
        created_by=None,
        created_at=at,
        updated_at=at,
    )


def _field(
    field_id: str,
    key: str,
    at: datetime,
    *,
    field_type: str = "text",
    required: bool = False,
    role: str | None = None,
    position: int = 0,
) -> DataFieldRow:
    """Build one active field definition."""
    return DataFieldRow(
        id=field_id,
        key=key,
        label=key.title(),
        description=None,
        field_type=field_type,
        options_json=None,
        required=required,
        position=position,
        role=role,
        archived_at=None,
        created_by="member:admin",
        created_at=at,
        updated_at=at,
    )


def _value(
    asset_id: str,
    field_id: str,
    value_json: str,
    at: datetime,
    value_date: str | None = None,
) -> DataFieldValueRow:
    """Build one field value row."""
    return DataFieldValueRow(
        asset_id=asset_id,
        field_id=field_id,
        value_json=value_json,
        value_date=value_date,
        updated_by="member:admin",
        updated_at=at,
    )


@pytest.mark.asyncio
async def test_query_tabs_filters_paging_required_fields_and_stale_owners() -> None:
    """Computed tabs, exact filters, keyset paging, and stale owner reads work together."""
    harness: SqliteHarness = await make_sqlite_harness()
    today: date = date(2026, 10, 13)
    at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    actors: RecordingActors = RecordingActors([])
    queries: AssetQueries = AssetQueries(EmptyIssues(), actors)
    try:
        async with harness.sessions.begin() as session:
            alpha: DataAssetRow = _asset("alpha", 1, "Alpha", at)
            beta: DataAssetRow = _asset("beta", 2, "Beta", at)
            gamma: DataAssetRow = _asset("gamma", 3, "Gamma", at, "deleted")
            owner: DataFieldRow = _field("owner", "owner", at, field_type="reference", role="owner")
            due: DataFieldRow = _field("due", "due", at, field_type="date", role="deletion_due")
            purpose: DataFieldRow = _field("purpose", "purpose", at, required=True, position=1)
            tags: DataFieldRow = _field("tags", "tags", at, field_type="multi_select", position=2)
            session.add_all((alpha, beta, gamma, owner, due, purpose, tags))
            await session.flush()
            session.add_all(
                (
                    _value(alpha.id, owner.id, '"member:retired"', at),
                    _value(beta.id, owner.id, '"team:retired"', at),
                    _value(gamma.id, owner.id, '"team:retired"', at),
                    _value(alpha.id, due.id, '"2026-10-12"', at, "2026-10-12"),
                    _value(beta.id, due.id, '"2026-10-13"', at, "2026-10-13"),
                    _value(gamma.id, due.id, '"2026-10-12"', at, "2026-10-12"),
                    _value(beta.id, purpose.id, '"Operations"', at),
                    _value(gamma.id, purpose.id, '"Archive"', at),
                    _value(
                        alpha.id,
                        tags.id,
                        dump_json(json_array((json_str("blue"), json_str("green")))),
                        at,
                    ),
                )
            )
            await session.flush()

            counts: CountsView = await queries.counts(session, AssetFilters(), today)
            assert (
                counts.all,
                counts.present,
                counts.deleted,
                counts.incomplete,
                counts.overdue,
            ) == (
                3,
                2,
                1,
                1,
                1,
            )
            assert tuple(
                asset.id
                for asset in (
                    await queries.list(session, AssetFilters(tab="incomplete"), today)
                ).items
            ) == ("alpha",)
            assert tuple(
                asset.id
                for asset in (await queries.list(session, AssetFilters(tab="overdue"), today)).items
            ) == ("alpha",)
            assert tuple(
                asset.id
                for asset in (await queries.list(session, AssetFilters(tab="deleted"), today)).items
            ) == ("gamma",)
            assert tuple(
                asset.id
                for asset in (await queries.list(session, AssetFilters(tab="present"), today)).items
            ) == ("alpha", "beta")
            assert (
                len(
                    (await queries.list(session, AssetFilters(owner="member:retired"), today)).items
                )
                == 1
            )
            assert (
                len(
                    (
                        await queries.list(
                            session,
                            AssetFilters(fields=(FieldFilter("tags", "green"),)),
                            today,
                        )
                    ).items
                )
                == 1
            )

            page_one: AssetPage = await queries.list(
                session, AssetFilters(sort="name", limit=2), today
            )
            assert tuple(asset.name for asset in page_one.items) == ("Alpha", "Beta")
            assert page_one.next_cursor is not None
            page_two: AssetPage = await queries.list(
                session,
                AssetFilters(sort="name", limit=2, cursor=page_one.next_cursor),
                today,
            )
            assert tuple(asset.name for asset in page_two.items) == ("Gamma",)
            descending: AssetPage = await queries.list(
                session, AssetFilters(sort="-item_count", limit=2), today
            )
            assert len(descending.items) == 2
            for sort in (
                "last_seen",
                "first_seen",
                "name",
                "item_count",
                "deletion_due",
                "short_id",
            ):
                first: AssetPage = await queries.list(
                    session, AssetFilters(sort=sort, limit=2), today
                )
                assert first.next_cursor is not None
                second: AssetPage = await queries.list(
                    session,
                    AssetFilters(sort=sort, limit=2, cursor=first.next_cursor),
                    today,
                )
                assert len(first.items + second.items) == 3
                assert {asset.id for asset in first.items + second.items} == {
                    "alpha",
                    "beta",
                    "gamma",
                }

            detail: AssetDetailView | None = await queries.detail(session, alpha.id, today)
            assert detail is not None
            assert detail.missing_required == ("purpose",)
            assert tuple(field.key for field in detail.fields) == (
                "owner",
                "due",
                "purpose",
                "tags",
            )
            assert detail.asset.owner is not None
            assert detail.asset.owner.ref == "member:retired"
            assert detail.asset.owner.display_name == "Unknown"
            assert actors.calls[-1] == ("member:retired",)
            all_page: AssetPage = await queries.list(session, AssetFilters(), today)
            assert len(all_page.items) == 3
            assert actors.calls[-1] == ("member:retired", "team:retired")
            team_detail: AssetDetailView | None = await queries.detail(session, beta.id, today)
            assert team_detail is not None
            assert team_detail.asset.owner is not None
            assert team_detail.asset.owner.ref == "team:retired"
            assert team_detail.asset.owner.display_name == "Unknown"
            exporter: AssetExporter = AssetExporter(queries, Redactor.default())
            member_export: CsvExport = await exporter.export(
                session, AssetFilters(owner="member:retired"), today
            )
            team_export: CsvExport = await exporter.export(
                session, AssetFilters(owner="team:retired"), today
            )
            assert member_export.row_count == 1 and "Unknown" in member_export.text
            assert team_export.row_count == 2 and "Unknown" in team_export.text
    finally:
        await harness.close()


def test_asyncpg_date_sort_cursors_bind_timezone_aware() -> None:
    """Date keyset parameters compile as PostgreSQL timestamptz for either direction."""
    assert _cursor_datetime("2026-01-01T00:00:00").tzinfo is UTC
    cursor: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    for sort in ("last_seen", "-last_seen", "first_seen", "-first_seen"):
        column: ColumnElement[str] | ColumnElement[int] | ColumnElement[datetime] = _sort_column(
            sort.lstrip("-")
        )
        predicate: ColumnElement[bool] = (
            column < cursor if sort.startswith("-") else column > cursor
        )
        sql: str = str(select(DataAssetRow.id).where(predicate).compile(dialect=asyncpg.dialect()))
        assert "TIMESTAMP WITH TIME ZONE" in sql
        assert "TIMESTAMP WITHOUT TIME ZONE" not in sql


@pytest.mark.asyncio
async def test_field_filters_match_typed_json_and_escape_multiselect_wildcards() -> None:
    """Typed equality, literal multi-select containment, and counts share predicates."""
    harness: SqliteHarness = await make_sqlite_harness()
    today: date = date(2026, 10, 13)
    at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    queries: AssetQueries = AssetQueries(EmptyIssues(), RecordingActors([]))
    exporter: AssetExporter = AssetExporter(queries, Redactor.default())
    try:
        async with harness.sessions.begin() as session:
            asset: DataAssetRow = _asset("typed", 11, "Typed", at)
            integer: DataFieldRow = _field("integer", "retention_years", at, field_type="integer")
            boolean: DataFieldRow = _field("boolean", "contains_pii", at, field_type="boolean")
            tags: DataFieldRow = _field("tags", "tags", at, field_type="multi_select")
            owner: DataFieldRow = _field(
                "quoted-owner", "owner", at, field_type="reference", role="owner"
            )
            session.add_all((asset, integer, boolean, tags, owner))
            await session.flush()
            session.add_all(
                (
                    _value(asset.id, integer.id, "5", at),
                    _value(asset.id, boolean.id, "true", at),
                    _value(asset.id, tags.id, '["piixa"]', at),
                    _value(asset.id, owner.id, dump_json(json_str('member:quote"owner')), at),
                )
            )
            await session.flush()
            for field_filter in (
                FieldFilter("retention_years", "5"),
                FieldFilter("contains_pii", "true"),
            ):
                filters: AssetFilters = AssetFilters(fields=(field_filter,))
                page: AssetPage = await queries.list(session, filters, today)
                counts: CountsView = await queries.counts(session, filters, today)
                assert tuple(item.id for item in page.items) == (asset.id,)
                assert counts.all == len(page.items) == 1
                exported: CsvExport = await exporter.export(session, filters, today)
                assert exported.row_count == 1

            underscore_filter: AssetFilters = AssetFilters(fields=(FieldFilter("tags", "pii_a"),))
            underscore_page: AssetPage = await queries.list(session, underscore_filter, today)
            underscore_counts: CountsView = await queries.counts(session, underscore_filter, today)
            assert underscore_page.items == ()
            assert underscore_counts.all == 0

            exact_filter: AssetFilters = AssetFilters(fields=(FieldFilter("tags", "piixa"),))
            exact_page: AssetPage = await queries.list(session, exact_filter, today)
            assert tuple(item.id for item in exact_page.items) == (asset.id,)
            owner_page: AssetPage = await queries.list(
                session, AssetFilters(owner='member:quote"owner'), today
            )
            assert tuple(item.id for item in owner_page.items) == (asset.id,)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_item_trend_and_series_forward_fill_event_counts() -> None:
    """List trends and item series retain a prior count and apply later events."""
    harness: SqliteHarness = await make_sqlite_harness()
    today: date = date(2026, 10, 13)
    at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    queries: AssetQueries = AssetQueries(EmptyIssues(), RecordingActors([]))
    try:
        async with harness.sessions.begin() as session:
            asset: DataAssetRow = _asset("alpha", 1, "Alpha", at)
            session.add(asset)
            await session.flush()
            session.add_all(
                (
                    DataAssetEventRow(
                        id="event-old",
                        asset_id=asset.id,
                        kind="items_changed",
                        occurred_at=None,
                        recorded_at=datetime(2026, 10, 1, tzinfo=UTC),
                        actor_ref="system:inventory",
                        item_count_after=7,
                    ),
                    DataAssetEventRow(
                        id="event-new",
                        asset_id=asset.id,
                        kind="items_changed",
                        occurred_at=None,
                        recorded_at=datetime(2026, 10, 12, tzinfo=UTC),
                        actor_ref="system:inventory",
                        item_count_after=5,
                    ),
                )
            )
            await session.flush()
            page: AssetPage = await queries.list(session, AssetFilters(), today)
            trend: tuple[int, ...] = page.items[0].item_trend
            assert len(trend) == 14
            assert trend[0] == 0
            assert trend[1:12] == (7,) * 11
            assert trend[12:] == (5, 5)
            series: tuple[SeriesPoint, ...] = await queries.item_series(session, asset.id, 3, today)
            assert tuple(point.item_count for point in series) == (7, 5)
    finally:
        await harness.close()
