"""Apply safe item and finding fingerprint diffs to data assets."""

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.ids import new_id
from telescreen.core.vocabulary import DataItemKind
from telescreen.db.models import DataAssetItemRow, DataAssetRow


@dataclass(frozen=True)
class ItemDelta:
    """Counts resulting from a fingerprint diff."""

    added: int
    removed: int
    open_count: int


@dataclass(frozen=True)
class CountDelta:
    """Net count change, including whether this establishes a baseline."""

    added: int
    removed: int
    after: int
    baseline: bool = False


@dataclass(frozen=True)
class _FingerprintRows:
    """Rows indexed by sorted fingerprints without a forbidden mapping type."""

    keys: tuple[str, ...]
    rows: tuple[DataAssetItemRow, ...]

    def get(self, fingerprint: str) -> DataAssetItemRow | None:
        """Find one row in logarithmic time."""
        index: int = bisect_left(self.keys, fingerprint)
        if index < len(self.keys) and self.keys[index] == fingerprint:
            return self.rows[index]
        return None


@dataclass(frozen=True)
class _FingerprintVersions:
    """Versions indexed by sorted fingerprints without a mapping type."""

    keys: tuple[str, ...]
    versions: tuple[int, ...]

    def get(self, fingerprint: str) -> int | None:
        """Find one version in logarithmic time."""
        index: int = bisect_left(self.keys, fingerprint)
        if index < len(self.keys) and self.keys[index] == fingerprint:
            return self.versions[index]
        return None


async def apply_fingerprints(
    session: AsyncSession,
    asset: DataAssetRow,
    kind: DataItemKind,
    fingerprints: frozenset[str],
    key_version: int,
    complete: bool,
    at: datetime,
) -> ItemDelta:
    """Add and reopen fingerprints, inferring removals only on complete scans."""
    rows: tuple[DataAssetItemRow, ...] = tuple(
        (
            await session.scalars(
                select(DataAssetItemRow).where(
                    DataAssetItemRow.asset_id == asset.id,
                    DataAssetItemRow.kind == kind.value,
                )
            )
        ).all()
    )
    row_index: _FingerprintRows = _index_rows(rows)
    added: int = 0
    for fingerprint in fingerprints:
        row: DataAssetItemRow | None = row_index.get(fingerprint)
        if row is None:
            session.add(
                DataAssetItemRow(
                    id=new_id(),
                    asset_id=asset.id,
                    kind=kind.value,
                    fingerprint=fingerprint,
                    key_version=key_version,
                    first_seen_at=at,
                    removed_at=None,
                )
            )
            added += 1
        elif row.removed_at is not None:
            row.removed_at = None
            row.key_version = key_version
            added += 1
    removed: int = 0
    if complete:
        for row in rows:
            if row.removed_at is None and row.fingerprint not in fingerprints:
                row.removed_at = at
                removed += 1
    await session.flush()
    open_count: int = await _open_count(session, asset.id, kind)
    if kind is DataItemKind.ITEM:
        asset.item_count = open_count
    return ItemDelta(added, removed, open_count)


def apply_count(asset: DataAssetRow, count: int, complete: bool) -> CountDelta | None:
    """Apply a count-only observation when it is complete."""
    if not complete:
        return None
    previous: int | None = asset.item_count
    asset.item_count = count
    if previous is None:
        return CountDelta(0, 0, count, True)
    difference: int = count - previous
    return CountDelta(max(difference, 0), max(-difference, 0), count)


async def apply_values(
    session: AsyncSession,
    asset: DataAssetRow,
    values: frozenset[tuple[str, int]],
    complete: bool,
    at: datetime,
) -> ItemDelta | None:
    """Diff finding fingerprints and silently rebaseline after key rotation."""
    rows: tuple[DataAssetItemRow, ...] = tuple(
        (
            await session.scalars(
                select(DataAssetItemRow).where(
                    DataAssetItemRow.asset_id == asset.id,
                    DataAssetItemRow.kind == DataItemKind.VALUE.value,
                )
            )
        ).all()
    )
    row_index: _FingerprintRows = _index_rows(rows)
    ordered_values: tuple[tuple[str, int], ...] = tuple(sorted(values))
    value_index: _FingerprintVersions = _index_versions(ordered_values)
    versions: frozenset[int] = frozenset(version for _, version in values)
    if complete and len(versions) == 1:
        version: int = next(iter(versions))
        opened: tuple[DataAssetItemRow, ...] = tuple(row for row in rows if row.removed_at is None)
        if opened and all(row.key_version != version for row in opened):
            for row in opened:
                replacement_version: int | None = value_index.get(row.fingerprint)
                if replacement_version is None:
                    row.removed_at = at
                else:
                    row.key_version = replacement_version
            for fingerprint, item_version in values:
                existing: DataAssetItemRow | None = row_index.get(fingerprint)
                if existing is None:
                    session.add(
                        DataAssetItemRow(
                            id=new_id(),
                            asset_id=asset.id,
                            kind=DataItemKind.VALUE.value,
                            fingerprint=fingerprint,
                            key_version=item_version,
                            first_seen_at=at,
                            removed_at=None,
                        )
                    )
                else:
                    existing.removed_at = None
                    existing.key_version = item_version
            await session.flush()
            return None
    fingerprints: frozenset[str] = frozenset(fingerprint for fingerprint, _ in values)
    added: int = 0
    for fingerprint, item_version in values:
        row: DataAssetItemRow | None = row_index.get(fingerprint)
        if row is None:
            session.add(
                DataAssetItemRow(
                    id=new_id(),
                    asset_id=asset.id,
                    kind=DataItemKind.VALUE.value,
                    fingerprint=fingerprint,
                    key_version=item_version,
                    first_seen_at=at,
                    removed_at=None,
                )
            )
            added += 1
        elif row.removed_at is not None:
            row.removed_at = None
            row.key_version = item_version
            added += 1
    removed: int = 0
    if complete:
        for row in rows:
            if row.removed_at is None and row.fingerprint not in fingerprints:
                row.removed_at = at
                removed += 1
    await session.flush()
    open_count: int = await _open_count(session, asset.id, DataItemKind.VALUE)
    return ItemDelta(added, removed, open_count)


async def close_all(session: AsyncSession, asset: DataAssetRow, at: datetime) -> tuple[int, int]:
    """Close open list items and finding fingerprints for an asset."""
    rows: tuple[DataAssetItemRow, ...] = tuple(
        (
            await session.scalars(
                select(DataAssetItemRow).where(
                    DataAssetItemRow.asset_id == asset.id, DataAssetItemRow.removed_at.is_(None)
                )
            )
        ).all()
    )
    item_count: int = 0
    value_count: int = 0
    for row in rows:
        row.removed_at = at
        if row.kind == DataItemKind.ITEM.value:
            item_count += 1
        elif row.kind == DataItemKind.VALUE.value:
            value_count += 1
    await session.flush()
    return item_count, value_count


async def _open_count(session: AsyncSession, asset_id: str, kind: DataItemKind) -> int:
    """Count open fingerprint rows for an asset and kind."""
    count: int | None = await session.scalar(
        select(func.count())
        .select_from(DataAssetItemRow)
        .where(
            DataAssetItemRow.asset_id == asset_id,
            DataAssetItemRow.kind == kind.value,
            DataAssetItemRow.removed_at.is_(None),
        )
    )
    return count or 0


def _index_rows(rows: tuple[DataAssetItemRow, ...]) -> _FingerprintRows:
    """Build one binary-search index for rows loaded by a diff operation."""
    ordered_rows: tuple[DataAssetItemRow, ...] = tuple(
        sorted(rows, key=lambda row: row.fingerprint)
    )
    keys: tuple[str, ...] = tuple(row.fingerprint for row in ordered_rows)
    return _FingerprintRows(keys, ordered_rows)


def _index_versions(values: tuple[tuple[str, int], ...]) -> _FingerprintVersions:
    """Build one binary-search index for incoming fingerprint versions."""
    keys: tuple[str, ...] = tuple(fingerprint for fingerprint, _ in values)
    versions: tuple[int, ...] = tuple(version for _, version in values)
    return _FingerprintVersions(keys, versions)
