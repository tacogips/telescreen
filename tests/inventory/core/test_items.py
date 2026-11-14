"""Tests for inventory item count updates and CSV safety."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from telescreen.core.vocabulary import DataItemKind
from telescreen.db.models import DataAssetItemRow, DataAssetRow
from telescreen.inventory.export import _csv_cell
from telescreen.inventory.items import (
    CountDelta,
    ItemDelta,
    apply_count,
    apply_fingerprints,
    apply_values,
)
from tests.support.db import SqliteHarness, make_sqlite_harness


def _asset(item_count: int | None) -> DataAssetRow:
    """Construct an in-memory asset row for pure item-count tests."""
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    return DataAssetRow(
        id="asset-id",
        number=1,
        resource_key="a" * 64,
        source="gdrive",
        origin="discovered",
        kind="file",
        state="present",
        name="Report",
        display_locator="drive item",
        collector_config_id=None,
        sweep_covered=False,
        item_count=item_count,
        first_seen_at=now,
        last_seen_at=now,
        deleted_at=None,
        overdue_notified_for=None,
        created_by=None,
        created_at=now,
        updated_at=now,
    )


def test_count_diff_updates_only_complete_observations() -> None:
    """Incomplete count data cannot overwrite the last complete count."""
    asset: DataAssetRow = _asset(10)

    assert apply_count(asset, 2, False) is None
    assert asset.item_count == 10

    delta: CountDelta | None = apply_count(asset, 7, True)
    assert delta is not None
    assert (delta.added, delta.removed, delta.after, delta.baseline) == (0, 3, 7, False)
    assert asset.item_count == 7


def test_first_complete_count_is_a_baseline() -> None:
    """The first count observation establishes a baseline without a net delta."""
    asset: DataAssetRow = _asset(None)

    delta: CountDelta | None = apply_count(asset, 10, True)

    assert delta is not None
    assert (delta.added, delta.removed, delta.after, delta.baseline) == (0, 0, 10, True)
    assert asset.item_count == 10


def test_csv_cell_protects_formulas_and_quotes_rfc4180_content() -> None:
    """CSV formula prefixes are neutralized and embedded delimiters are quoted."""
    assert _csv_cell("=cmd") == "'=cmd"
    assert _csv_cell('name, "quoted"') == '"name, ""quoted"""'
    assert _csv_cell("plain") == "plain"


@pytest.mark.asyncio
async def test_value_key_version_change_silently_rebaselines() -> None:
    """A complete version change closes stale values without returning a diff."""
    harness: SqliteHarness = await make_sqlite_harness()
    at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    try:
        async with harness.sessions.begin() as session:
            asset: DataAssetRow = _asset(None)
            session.add(asset)
            await session.flush()
            initial: ItemDelta | None = await apply_values(
                session, asset, frozenset((("a" * 64, 1),)), True, at
            )
            assert initial is not None
            rebased: ItemDelta | None = await apply_values(
                session, asset, frozenset((("b" * 64, 2),)), True, at
            )
            assert rebased is None
            rows: tuple[DataAssetItemRow, ...] = tuple(
                (await session.scalars(select(DataAssetItemRow))).all()
            )
            assert len(rows) == 2
            assert next(row for row in rows if row.fingerprint == "a" * 64).removed_at is not None
            new_row: DataAssetItemRow = next(row for row in rows if row.fingerprint == "b" * 64)
            assert new_row.removed_at is None
            assert new_row.key_version == 2
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_twenty_thousand_fingerprint_diff_handles_shifted_complete_list() -> None:
    """A contract-sized shifted list preserves exact add/remove/open counts."""
    harness: SqliteHarness = await make_sqlite_harness()
    at: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    initial_fingerprints: frozenset[str] = frozenset(f"{index:064x}" for index in range(20_000))
    shifted_fingerprints: frozenset[str] = frozenset(f"{index:064x}" for index in range(1, 20_001))
    try:
        async with harness.sessions.begin() as session:
            asset: DataAssetRow = _asset(None)
            session.add(asset)
            await session.flush()
            initial: ItemDelta = await apply_fingerprints(
                session,
                asset,
                DataItemKind.ITEM,
                initial_fingerprints,
                1,
                True,
                at,
            )
            assert (initial.added, initial.removed, initial.open_count) == (20_000, 0, 20_000)
            shifted: ItemDelta = await apply_fingerprints(
                session,
                asset,
                DataItemKind.ITEM,
                shifted_fingerprints,
                1,
                True,
                at,
            )
            assert (shifted.added, shifted.removed, shifted.open_count) == (1, 1, 20_000)
    finally:
        await harness.close()
