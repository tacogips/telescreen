"""Redacted and formula-safe CSV export of asset list pages."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonKind, JsonValue, dump_json, parse_json
from telescreen.db.models import DataFieldRow, DataFieldValueRow
from telescreen.inventory.queries import AssetFilters, AssetPage, AssetQueries, AssetView
from telescreen.masking.redact import Redactor


@dataclass(frozen=True)
class CsvExport:
    """CSV payload and truncation metadata."""

    text: str
    row_count: int
    truncated: bool


@dataclass(frozen=True)
class AssetExporter:
    """Render masked asset list rows as RFC 4180 CSV."""

    queries: AssetQueries
    redactor: Redactor

    async def export(self, session: AsyncSession, filters: AssetFilters, today: date) -> CsvExport:
        """Export at most 10,000 filtered assets with safe formula handling."""
        fields: tuple[DataFieldRow, ...] = tuple(
            (
                await session.scalars(
                    select(DataFieldRow)
                    .where(DataFieldRow.archived_at.is_(None))
                    .order_by(DataFieldRow.position)
                )
            ).all()
        )
        columns: tuple[str, ...] = (
            "short_id",
            "name",
            "source",
            "display_locator",
            "kind",
            "origin",
            "state",
            "item_count",
            "open_issue_count",
            "owner",
            "deletion_due",
            "overdue",
            "incomplete",
            "first_seen_at",
            "last_seen_at",
            "deleted_at",
        ) + tuple(f"field:{field.key}" for field in fields)
        lines: list[str] = [_csv_row(columns)]
        page_filters: AssetFilters = AssetFilters(
            tab=filters.tab,
            q=filters.q,
            source=filters.source,
            kind=filters.kind,
            state=filters.state,
            origin=filters.origin,
            owner=filters.owner,
            collector_config_id=filters.collector_config_id,
            fields=filters.fields,
            sort=filters.sort,
            limit=200,
            cursor=filters.cursor,
        )
        count: int = 0
        truncated: bool = False
        while count < 10000:
            page: AssetPage = await self.queries.list(session, page_filters, today)
            if not page.items:
                break
            for asset in page.items:
                if count >= 10000:
                    truncated = True
                    break
                field_values: tuple[str, ...] = await self._field_values(session, asset)
                cells: tuple[str, ...] = _asset_cells(asset) + field_values
                masked: tuple[str, ...] = tuple(self.redactor.redact(cell).text for cell in cells)
                lines.append(_csv_row(masked))
                count += 1
            if count == 10000 and page.next_cursor is not None:
                truncated = True
                break
            if page.next_cursor is None:
                break
            page_filters = AssetFilters(
                tab=filters.tab,
                q=filters.q,
                source=filters.source,
                kind=filters.kind,
                state=filters.state,
                origin=filters.origin,
                owner=filters.owner,
                collector_config_id=filters.collector_config_id,
                fields=filters.fields,
                sort=filters.sort,
                limit=200,
                cursor=page.next_cursor,
            )
        return CsvExport("\r\n".join(lines) + "\r\n", count, truncated)

    async def _field_values(self, session: AsyncSession, asset: AssetView) -> tuple[str, ...]:
        """Read metadata values in active field order, with select arrays joined."""
        fields: tuple[DataFieldRow, ...] = tuple(
            (
                await session.scalars(
                    select(DataFieldRow)
                    .where(DataFieldRow.archived_at.is_(None))
                    .order_by(DataFieldRow.position)
                )
            ).all()
        )
        values: tuple[DataFieldValueRow, ...] = tuple(
            (
                await session.scalars(
                    select(DataFieldValueRow).where(DataFieldValueRow.asset_id == asset.id)
                )
            ).all()
        )
        result: list[str] = []
        for field in fields:
            value: DataFieldValueRow | None = next(
                (row for row in values if row.field_id == field.id), None
            )
            result.append(
                _field_display(value.value_json, field.field_type) if value is not None else ""
            )
        return tuple(result)


def _asset_cells(asset: AssetView) -> tuple[str, ...]:
    """Return base CSV columns as safe display strings."""
    return (
        asset.short_id,
        asset.name,
        asset.source,
        asset.display_locator,
        asset.kind,
        asset.origin,
        asset.state,
        _optional(asset.item_count),
        str(asset.open_issue_count),
        asset.owner.display_name if asset.owner is not None else "",
        asset.deletion_due or "",
        str(asset.overdue).lower(),
        str(asset.incomplete).lower(),
        asset.first_seen_at.isoformat(),
        asset.last_seen_at.isoformat() if asset.last_seen_at is not None else "",
        asset.deleted_at.isoformat() if asset.deleted_at is not None else "",
    )


def _optional(value: int | None) -> str:
    """Format an optional integer cell."""
    return str(value) if value is not None else ""


def _field_display(value_json: str, field_type: str) -> str:
    """Format a canonical field value for CSV display."""
    value: JsonValue = parse_json(value_json)
    if field_type == "multi_select" and value.kind is JsonKind.ARRAY:
        return ";".join(item.str_value for item in value.items)
    if value.kind is JsonKind.STRING:
        return value.str_value
    return dump_json(value)


def _csv_row(cells: tuple[str, ...]) -> str:
    """Encode one CSV row using RFC 4180 quoting and formula protection."""
    return ",".join(_csv_cell(cell) for cell in cells)


def _csv_cell(value: str) -> str:
    """Escape one cell and prefix formula-like leading characters."""
    safe: str = "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
    escaped: str = safe.replace('"', '""')
    return f'"{escaped}"' if any(char in safe for char in (",", '"', "\r", "\n")) else safe
