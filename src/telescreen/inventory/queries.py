"""Typed data asset list, detail, event, and item queries."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import (
    DateTime,
    Integer,
    String,
    and_,
    cast,
    exists,
    func,
    or_,
    select,
    true,
    type_coerce,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect, Select

from telescreen.api.common.pagination import decode_cursor, encode_cursor
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, dump_json, json_str, parse_json
from telescreen.core.vocabulary import DataAssetState, ErrorCode
from telescreen.db.models import (
    DataAssetEventRow,
    DataAssetItemRow,
    DataAssetRow,
    DataFieldRow,
    DataFieldValueRow,
)
from telescreen.ports.directory import ActorDirectory, ActorDisplay
from telescreen.ports.issues import ResourceIssueReader, ResourceIssueRef


@dataclass(frozen=True)
class FieldFilter:
    """One exact metadata field filter."""

    key: str
    value: str


@dataclass(frozen=True)
class AssetFilters:
    """Validated filters and keyset page settings for asset queries."""

    tab: str = "all"
    q: str | None = None
    source: str | None = None
    kind: str | None = None
    state: str | None = None
    origin: str | None = None
    owner: str | None = None
    collector_config_id: str | None = None
    fields: tuple[FieldFilter, ...] = ()
    sort: str = "last_seen"
    limit: int = 50
    cursor: str | None = None


@dataclass(frozen=True)
class OwnerView:
    """Stored owner reference and safe display name."""

    ref: str
    display_name: str


@dataclass(frozen=True)
class AssetView:
    """API projection for a data asset row."""

    id: str
    short_id: str
    number: int
    name: str
    source: str
    display_locator: str
    kind: str
    origin: str
    state: str
    item_count: int | None
    item_trend: tuple[int, ...]
    open_issue_count: int
    owner: OwnerView | None
    deletion_due: str | None
    overdue: bool
    incomplete: bool
    first_seen_at: datetime
    last_seen_at: datetime | None
    deleted_at: datetime | None
    collector_config_id: str | None


@dataclass(frozen=True)
class FieldValueView:
    """One non-archived metadata field and its current value."""

    key: str
    label: str
    field_type: str
    required: bool
    role: str | None
    options: JsonValue | None
    value_json: str | None


@dataclass(frozen=True)
class ActorView:
    """Safe actor display for the asset creator."""

    kind: str
    ref: str
    display_name: str


@dataclass(frozen=True)
class IssueView:
    """Compact open issue reference."""

    issue_id: str
    short_id: str


@dataclass(frozen=True)
class AssetDetailView:
    """Extended data asset projection."""

    asset: AssetView
    sweep_covered: bool
    created_by: ActorView | None
    value_count: int
    fields: tuple[FieldValueView, ...]
    missing_required: tuple[str, ...]
    open_issues: tuple[IssueView, ...]


@dataclass(frozen=True)
class EventView:
    """Safe event projection."""

    id: str
    kind: str
    occurred_at: datetime | None
    recorded_at: datetime
    actor: ActorView
    items_added: int | None
    items_removed: int | None
    item_count_after: int | None
    values_added: int | None
    values_removed: int | None
    value_count_after: int | None
    field_key: str | None
    old_value: str | None
    new_value: str | None
    note: str | None


@dataclass(frozen=True)
class ItemView:
    """Safe item fingerprint prefix and observation timestamps."""

    id: str
    kind: str
    key_prefix: str
    first_seen_at: datetime
    removed_at: datetime | None


@dataclass(frozen=True)
class SeriesPoint:
    """One item-count series point."""

    at: datetime
    item_count: int


@dataclass(frozen=True)
class AssetPage:
    """One asset page and its next cursor."""

    items: tuple[AssetView, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class CountsView:
    """Asset counts for list tabs."""

    all: int
    present: int
    deleted: int
    incomplete: int
    overdue: int


@dataclass(frozen=True)
class EventPage:
    """One newest-first event page."""

    items: tuple[EventView, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ItemPage:
    """One fingerprint item page."""

    items: tuple[ItemView, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class AssetQueries:
    """Query assets while batching issue and actor enrichment per page."""

    issues: ResourceIssueReader
    actors: ActorDirectory

    async def list(self, session: AsyncSession, filters: AssetFilters, today: date) -> AssetPage:
        """Return an enriched asset page with keyset continuation."""
        query: Select[tuple[DataAssetRow]] = select(DataAssetRow).where(
            self._predicates(filters, today)
        )
        sort_key: str = filters.sort.lstrip("-")
        descending: bool = filters.sort.startswith("-")
        column: ColumnElement[str] | ColumnElement[int] | ColumnElement[datetime] = _sort_column(
            sort_key
        )
        if filters.cursor is not None:
            cursor_parts: tuple[str, ...] = _cursor_parts(filters.cursor, 2)
            cursor_text: str = cursor_parts[0]
            cursor_value: str | int | datetime = cursor_text
            if sort_key in ("item_count", "short_id"):
                try:
                    cursor_value = int(cursor_text)
                except ValueError:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
            elif sort_key in ("first_seen", "last_seen"):
                cursor_value = _cursor_datetime(cursor_text)
            cursor_id: str = cursor_parts[1]
            key_predicate: ColumnElement[bool] = (
                column < cursor_value if descending else column > cursor_value
            )
            tie_predicate: ColumnElement[bool] = (
                DataAssetRow.id < cursor_id if descending else DataAssetRow.id > cursor_id
            )
            query = query.where(or_(key_predicate, and_(column == cursor_value, tie_predicate)))
        if descending:
            query = query.order_by(column.desc(), DataAssetRow.id.desc())
        else:
            query = query.order_by(column.asc(), DataAssetRow.id.asc())
        rows: tuple[DataAssetRow, ...] = tuple(
            (await session.scalars(query.limit(filters.limit + 1))).all()
        )
        has_more: bool = len(rows) > filters.limit
        page_rows: tuple[DataAssetRow, ...] = rows[: filters.limit]
        views: tuple[AssetView, ...] = await self._asset_views(session, page_rows, today)
        next_cursor: str | None = None
        if has_more and page_rows:
            last: DataAssetRow = page_rows[-1]
            last_view: AssetView = views[-1]
            sort_value: str = (
                last_view.deletion_due or "9999-12-31"
                if sort_key == "deletion_due"
                else _sort_value(last, sort_key)
            )
            next_cursor = encode_cursor((sort_value, last.id))
        return AssetPage(views, next_cursor)

    async def counts(self, session: AsyncSession, filters: AssetFilters, today: date) -> CountsView:
        """Count assets by list tab using the same predicates as list."""
        base: AssetFilters = AssetFilters(
            q=filters.q,
            source=filters.source,
            kind=filters.kind,
            state=filters.state,
            origin=filters.origin,
            owner=filters.owner,
            collector_config_id=filters.collector_config_id,
            fields=filters.fields,
        )
        base_predicate: ColumnElement[bool] = self._predicates(base, today)
        all_count: int = await self._count(session, base_predicate)
        present: int = await self._count(
            session, and_(base_predicate, DataAssetRow.state == DataAssetState.PRESENT.value)
        )
        deleted: int = await self._count(
            session, and_(base_predicate, DataAssetRow.state == DataAssetState.DELETED.value)
        )
        incomplete: int = await self._count(
            session, and_(base_predicate, self._incomplete_predicate())
        )
        overdue: int = await self._count(
            session, and_(base_predicate, self._overdue_predicate(today))
        )
        return CountsView(all_count, present, deleted, incomplete, overdue)

    async def detail(
        self, session: AsyncSession, asset_id: str, today: date
    ) -> AssetDetailView | None:
        """Return asset detail with fields and open issue references."""
        asset: DataAssetRow | None = await session.get(DataAssetRow, asset_id)
        if asset is None:
            return None
        views: tuple[AssetView, ...] = await self._asset_views(session, (asset,), today)
        fields_and_values: tuple[
            tuple[DataFieldRow, DataFieldValueRow | None], ...
        ] = await self._fields_for_asset(session, asset.id)
        fields: tuple[FieldValueView, ...] = tuple(
            FieldValueView(
                field.key,
                field.label,
                field.field_type,
                field.required,
                field.role,
                parse_json(field.options_json) if field.options_json is not None else None,
                value.value_json if value is not None else None,
            )
            for field, value in fields_and_values
        )
        missing: tuple[str, ...] = tuple(
            field.key for field, value in fields_and_values if field.required and value is None
        )
        open_value_count: int = (
            await session.scalar(
                select(func.count())
                .select_from(DataAssetItemRow)
                .where(
                    DataAssetItemRow.asset_id == asset.id,
                    DataAssetItemRow.kind == "value",
                    DataAssetItemRow.removed_at.is_(None),
                )
            )
            or 0
        )
        issues: tuple[ResourceIssueRef, ...] = await self.issues.open_issues_for_resources(
            session, (asset.resource_key,)
        )
        actor_view: ActorView | None = None
        if asset.created_by is not None:
            actor: ActorDisplay = (await self.actors.display(session, (asset.created_by,)))[0]
            actor_view = ActorView(actor.kind.value, actor.ref, actor.display_name)
        return AssetDetailView(
            views[0],
            asset.sweep_covered,
            actor_view,
            open_value_count,
            fields,
            missing,
            tuple(IssueView(issue.issue_id, issue.short_id) for issue in issues),
        )

    async def by_resource_key(
        self, session: AsyncSession, key: str, today: date
    ) -> AssetView | None:
        """Look up one asset by opaque resource key."""
        asset: DataAssetRow | None = await session.scalar(
            select(DataAssetRow).where(DataAssetRow.resource_key == key)
        )
        if asset is None:
            return None
        return (await self._asset_views(session, (asset,), today))[0]

    async def events(
        self, session: AsyncSession, asset_id: str, limit: int, cursor: str | None
    ) -> EventPage:
        """Return newest-first asset events with keyset paging."""
        query: Select[tuple[DataAssetEventRow]] = select(DataAssetEventRow).where(
            DataAssetEventRow.asset_id == asset_id
        )
        if cursor is not None:
            cursor_parts: tuple[str, ...] = _cursor_parts(cursor, 2)
            cursor_at: datetime = _cursor_datetime(cursor_parts[0])
            cursor_id: str = cursor_parts[1]
            query = query.where(
                or_(
                    DataAssetEventRow.recorded_at < cursor_at,
                    and_(
                        DataAssetEventRow.recorded_at == cursor_at,
                        DataAssetEventRow.id < cursor_id,
                    ),
                )
            )
        rows: tuple[DataAssetEventRow, ...] = tuple(
            (
                await session.scalars(
                    query.order_by(
                        DataAssetEventRow.recorded_at.desc(), DataAssetEventRow.id.desc()
                    ).limit(limit + 1)
                )
            ).all()
        )
        has_more: bool = len(rows) > limit
        page_rows: tuple[DataAssetEventRow, ...] = rows[:limit]
        refs: tuple[str, ...] = tuple(sorted({row.actor_ref for row in page_rows}))
        actors: tuple[ActorDisplay, ...] = await self.actors.display(session, refs) if refs else ()
        page: tuple[EventView, ...] = tuple(
            EventView(
                row.id,
                row.kind,
                row.occurred_at,
                row.recorded_at,
                ActorView(
                    next(
                        (actor.kind.value for actor in actors if actor.ref == row.actor_ref),
                        "unknown",
                    ),
                    row.actor_ref,
                    next(
                        (actor.display_name for actor in actors if actor.ref == row.actor_ref),
                        "Unknown",
                    ),
                ),
                row.items_added,
                row.items_removed,
                row.item_count_after,
                row.values_added,
                row.values_removed,
                row.value_count_after,
                row.field_key,
                row.old_value,
                row.new_value,
                row.note,
            )
            for row in page_rows
        )
        next_cursor: str | None = (
            encode_cursor((page_rows[-1].recorded_at.isoformat(), page_rows[-1].id))
            if has_more and page_rows
            else None
        )
        return EventPage(page, next_cursor)

    async def items(
        self,
        session: AsyncSession,
        asset_id: str,
        kind: str,
        state: str,
        limit: int,
        cursor: str | None,
    ) -> ItemPage:
        """Return fingerprint prefixes for an asset's items or values."""
        query: Select[tuple[DataAssetItemRow]] = select(DataAssetItemRow).where(
            DataAssetItemRow.asset_id == asset_id, DataAssetItemRow.kind == kind
        )
        query = query.where(
            DataAssetItemRow.removed_at.is_(None)
            if state == "present"
            else DataAssetItemRow.removed_at.is_not(None)
        )
        if cursor is not None:
            cursor_parts: tuple[str, ...] = _cursor_parts(cursor, 1)
            query = query.where(DataAssetItemRow.id > cursor_parts[0])
        rows: tuple[DataAssetItemRow, ...] = tuple(
            (await session.scalars(query.order_by(DataAssetItemRow.id).limit(limit + 1))).all()
        )
        has_more: bool = len(rows) > limit
        page_rows: tuple[DataAssetItemRow, ...] = rows[:limit]
        page: tuple[ItemView, ...] = tuple(
            ItemView(row.id, row.kind, row.fingerprint[:12], row.first_seen_at, row.removed_at)
            for row in page_rows
        )
        next_cursor: str | None = (
            encode_cursor((page_rows[-1].id,)) if has_more and page_rows else None
        )
        return ItemPage(page, next_cursor)

    async def item_series(
        self, session: AsyncSession, asset_id: str, days: int, today: date
    ) -> tuple[SeriesPoint, ...]:
        """Return daily last-known item counts for the requested window."""
        events: tuple[DataAssetEventRow, ...] = tuple(
            (
                await session.scalars(
                    select(DataAssetEventRow)
                    .where(
                        DataAssetEventRow.asset_id == asset_id,
                        DataAssetEventRow.item_count_after.is_not(None),
                        DataAssetEventRow.recorded_at
                        < datetime.combine(today + timedelta(days=1), datetime.min.time(), UTC),
                    )
                    .order_by(DataAssetEventRow.recorded_at)
                )
            ).all()
        )
        start: date = today - timedelta(days=days - 1)
        prior: DataAssetEventRow | None = next(
            (event for event in reversed(events) if event.recorded_at.date() < start), None
        )
        points: list[SeriesPoint] = []
        if prior is not None and prior.item_count_after is not None:
            points.append(SeriesPoint(prior.recorded_at, prior.item_count_after))
        points.extend(
            SeriesPoint(event.recorded_at, event.item_count_after or 0)
            for event in events
            if event.recorded_at.date() >= start
        )
        return tuple(points)

    async def _asset_views(
        self, session: AsyncSession, rows: tuple[DataAssetRow, ...], today: date
    ) -> tuple[AssetView, ...]:
        """Enrich a page with one issue read and one owner display call."""
        keys: tuple[str, ...] = tuple(row.resource_key for row in rows)
        issue_refs: tuple[ResourceIssueRef, ...] = (
            await self.issues.open_issues_for_resources(session, keys) if keys else ()
        )
        issue_counts: tuple[tuple[str, int], ...] = tuple(
            (
                row.resource_key,
                sum(1 for issue in issue_refs if issue.resource_key == row.resource_key),
            )
            for row in rows
        )
        values: tuple[
            tuple[DataAssetRow, DataFieldRow, DataFieldValueRow], ...
        ] = await self._role_values(session, rows)
        refs: tuple[str, ...] = tuple(
            sorted(
                {
                    _json_string(value.value_json)
                    for _, field, value in values
                    if field.role == "owner"
                }
            )
        )
        actor_displays: tuple[ActorDisplay, ...] = (
            await self.actors.display(session, refs) if refs else ()
        )
        owner_names: tuple[tuple[str, str], ...] = tuple(
            (actor.ref, actor.display_name) for actor in actor_displays
        )
        output: list[AssetView] = []
        for row in rows:
            owner_value: str | None = next(
                (
                    _json_string(value.value_json)
                    for asset, field, value in values
                    if asset.id == row.id and field.role == "owner"
                ),
                None,
            )
            owner_view: OwnerView | None = None
            if owner_value is not None:
                name: str = next(
                    (display for ref, display in owner_names if ref == owner_value), "Unknown"
                )
                owner_view = OwnerView(owner_value, name)
            due: str | None = next(
                (
                    _json_string(value.value_json)
                    for asset, field, value in values
                    if asset.id == row.id and field.role == "deletion_due"
                ),
                None,
            )
            overdue: bool = (
                row.state != DataAssetState.DELETED.value
                and due is not None
                and due < today.isoformat()
            )
            incomplete: bool = await self._asset_incomplete(session, row.id)
            issue_count: int = next(
                (count for key, count in issue_counts if key == row.resource_key), 0
            )
            trend: tuple[int, ...] = await self._trend(session, row, today)
            output.append(
                AssetView(
                    row.id,
                    f"DATA-{row.number}",
                    row.number,
                    row.name,
                    row.source,
                    row.display_locator,
                    row.kind,
                    row.origin,
                    row.state,
                    row.item_count,
                    trend,
                    issue_count,
                    owner_view,
                    due,
                    overdue,
                    incomplete,
                    row.first_seen_at,
                    row.last_seen_at,
                    row.deleted_at,
                    row.collector_config_id,
                )
            )
        return tuple(output)

    async def _role_values(
        self, session: AsyncSession, rows: tuple[DataAssetRow, ...]
    ) -> tuple[tuple[DataAssetRow, DataFieldRow, DataFieldValueRow], ...]:
        """Load owner and deletion date values for an asset page."""
        if not rows:
            return ()
        result: list[tuple[DataAssetRow, DataFieldRow, DataFieldValueRow]] = []
        query: Select[tuple[DataFieldRow, DataFieldValueRow, DataAssetRow]] = (
            select(DataFieldRow, DataFieldValueRow, DataAssetRow)
            .join(DataFieldValueRow, DataFieldValueRow.field_id == DataFieldRow.id)
            .join(DataAssetRow, DataAssetRow.id == DataFieldValueRow.asset_id)
            .where(
                DataFieldRow.role.in_(("owner", "deletion_due")),
                DataFieldRow.archived_at.is_(None),
                DataAssetRow.id.in_(tuple(row.id for row in rows)),
            )
        )
        for field, value, asset in (await session.execute(query)).all():
            result.append((asset, field, value))
        return tuple(result)

    async def _fields_for_asset(
        self, session: AsyncSession, asset_id: str
    ) -> tuple[tuple[DataFieldRow, DataFieldValueRow | None], ...]:
        """Return visible field definitions and matching values in order."""
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
                    select(DataFieldValueRow).where(DataFieldValueRow.asset_id == asset_id)
                )
            ).all()
        )
        return tuple(
            (field, next((value for value in values if value.field_id == field.id), None))
            for field in fields
        )

    async def _asset_incomplete(self, session: AsyncSession, asset_id: str) -> bool:
        """Check whether any active required field lacks an asset value."""
        return bool(
            await session.scalar(
                select(
                    exists().where(
                        DataFieldRow.required.is_(True),
                        DataFieldRow.archived_at.is_(None),
                        ~exists().where(
                            DataFieldValueRow.field_id == DataFieldRow.id,
                            DataFieldValueRow.asset_id == asset_id,
                        ),
                    )
                )
            )
        )

    async def _count(self, session: AsyncSession, predicate: ColumnElement[bool]) -> int:
        """Count assets matching one SQL predicate."""
        return int(
            await session.scalar(select(func.count()).select_from(DataAssetRow).where(predicate))
            or 0
        )

    def _predicates(self, filters: AssetFilters, today: date) -> ColumnElement[bool]:
        """Build SQL predicates shared by list and count queries."""
        predicates: list[ColumnElement[bool]] = []
        if filters.tab == "present":
            predicates.append(DataAssetRow.state == DataAssetState.PRESENT.value)
        elif filters.tab == "deleted":
            predicates.append(DataAssetRow.state == DataAssetState.DELETED.value)
        elif filters.tab == "incomplete":
            predicates.append(self._incomplete_predicate())
        elif filters.tab == "overdue":
            predicates.append(self._overdue_predicate(today))
        if filters.state:
            predicates.append(DataAssetRow.state == filters.state)
        if filters.source:
            predicates.append(DataAssetRow.source == filters.source)
        if filters.kind:
            predicates.append(DataAssetRow.kind == filters.kind)
        if filters.origin:
            predicates.append(DataAssetRow.origin == filters.origin)
        if filters.collector_config_id:
            predicates.append(DataAssetRow.collector_config_id == filters.collector_config_id)
        if filters.owner:
            predicates.append(
                exists().where(
                    DataFieldRow.role == "owner",
                    DataFieldRow.archived_at.is_(None),
                    DataFieldValueRow.field_id == DataFieldRow.id,
                    DataFieldValueRow.asset_id == DataAssetRow.id,
                    DataFieldValueRow.value_json == dump_json(json_str(filters.owner)),
                )
            )
        for field_filter in filters.fields:
            canonical: str = dump_json(json_str(field_filter.value))
            value_predicates: list[ColumnElement[bool]] = [
                and_(
                    DataFieldRow.field_type != "multi_select",
                    DataFieldValueRow.value_json == canonical,
                )
            ]
            if re.fullmatch(r"-?[0-9]{1,16}", field_filter.value) is not None:
                value_predicates.append(
                    and_(
                        DataFieldRow.field_type == "integer",
                        DataFieldValueRow.value_json == field_filter.value,
                    )
                )
            if field_filter.value in ("true", "false"):
                value_predicates.append(
                    and_(
                        DataFieldRow.field_type == "boolean",
                        DataFieldValueRow.value_json == field_filter.value,
                    )
                )
            escaped: str = canonical.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            value_predicates.append(
                and_(
                    DataFieldRow.field_type == "multi_select",
                    DataFieldValueRow.value_json.like(f"%{escaped}%", escape="\\"),
                )
            )
            predicates.append(
                exists().where(
                    DataFieldRow.key == field_filter.key,
                    DataFieldRow.archived_at.is_(None),
                    DataFieldValueRow.field_id == DataFieldRow.id,
                    DataFieldValueRow.asset_id == DataAssetRow.id,
                    or_(*value_predicates),
                )
            )
        if filters.q:
            pattern: str = f"%{filters.q}%"
            predicates.append(
                or_(
                    DataAssetRow.name.ilike(pattern),
                    DataAssetRow.display_locator.ilike(pattern),
                    DataAssetRow.source.ilike(pattern),
                )
            )
        return and_(*predicates) if predicates else true()

    def _incomplete_predicate(self) -> ColumnElement[bool]:
        """Build the SQL predicate for missing required metadata."""
        return (
            exists()
            .where(
                DataFieldRow.required.is_(True),
                DataFieldRow.archived_at.is_(None),
                ~exists()
                .where(
                    DataFieldValueRow.field_id == DataFieldRow.id,
                    DataFieldValueRow.asset_id == DataAssetRow.id,
                )
                .correlate(DataFieldRow, DataAssetRow),
            )
            .correlate(DataAssetRow)
        )

    def _overdue_predicate(self, today: date) -> ColumnElement[bool]:
        """Build the SQL predicate for a non-deleted asset past its deletion date."""
        return and_(
            DataAssetRow.state != DataAssetState.DELETED.value,
            exists().where(
                DataFieldRow.role == "deletion_due",
                DataFieldRow.archived_at.is_(None),
                DataFieldValueRow.field_id == DataFieldRow.id,
                DataFieldValueRow.asset_id == DataAssetRow.id,
                DataFieldValueRow.value_date < today.isoformat(),
            ),
        )

    async def _trend(
        self, session: AsyncSession, asset: DataAssetRow, today: date
    ) -> tuple[int, ...]:
        """Return the last fourteen daily item counts, forward-filled."""
        if asset.item_count is None:
            return ()
        events: tuple[DataAssetEventRow, ...] = tuple(
            (
                await session.scalars(
                    select(DataAssetEventRow)
                    .where(
                        DataAssetEventRow.asset_id == asset.id,
                        DataAssetEventRow.item_count_after.is_not(None),
                        DataAssetEventRow.recorded_at
                        < datetime.combine(today + timedelta(days=1), datetime.min.time(), UTC),
                    )
                    .order_by(DataAssetEventRow.recorded_at)
                )
            ).all()
        )
        start: date = today - timedelta(days=13)
        prior: DataAssetEventRow | None = next(
            (event for event in reversed(events) if event.recorded_at.date() < start), None
        )
        count: int = prior.item_count_after or 0 if prior is not None else 0
        trend: list[int] = []
        event_index: int = next(
            (index for index, event in enumerate(events) if event.recorded_at.date() >= start),
            len(events),
        )
        for day_offset in range(14):
            day: date = start + timedelta(days=day_offset)
            while event_index < len(events) and events[event_index].recorded_at.date() <= day:
                count = events[event_index].item_count_after or 0
                event_index += 1
            trend.append(count)
        return tuple(trend)


def _sort_column(name: str) -> ColumnElement[str] | ColumnElement[int] | ColumnElement[datetime]:
    """Map an allow-listed sort field to its SQL column."""
    if name == "first_seen":
        return type_coerce(DataAssetRow.first_seen_at, DateTime(timezone=True))
    if name == "name":
        return cast(DataAssetRow.name, String)
    if name == "item_count":
        return cast(func.coalesce(DataAssetRow.item_count, -1), Integer)
    if name == "short_id":
        return cast(DataAssetRow.number, Integer)
    if name == "deletion_due":
        due: ScalarSelect[str | None] = (
            select(DataFieldValueRow.value_date)
            .join(DataFieldRow, DataFieldRow.id == DataFieldValueRow.field_id)
            .where(
                DataFieldRow.role == "deletion_due",
                DataFieldRow.archived_at.is_(None),
                DataFieldValueRow.asset_id == DataAssetRow.id,
            )
            .limit(1)
            .scalar_subquery()
        )
        return cast(func.coalesce(due, "9999-12-31"), String)
    return type_coerce(
        func.coalesce(DataAssetRow.last_seen_at, DataAssetRow.first_seen_at),
        DateTime(timezone=True),
    )


def _sort_value(asset: DataAssetRow, name: str) -> str:
    """Return the encoded sort key for an asset cursor."""
    if name == "name":
        return asset.name
    if name == "item_count":
        return str(asset.item_count if asset.item_count is not None else -1)
    if name == "short_id":
        return str(asset.number)
    if name == "deletion_due":
        return "9999-12-31"
    if name == "first_seen":
        return asset.first_seen_at.isoformat()
    return (asset.last_seen_at or asset.first_seen_at).isoformat()


def _json_string(value: str) -> str:
    """Extract a JSON string value while preserving safe plain text fallback."""
    parsed: JsonValue = parse_json(value)
    return parsed.str_value if parsed.kind.value == "string" else value


def _cursor_parts(cursor: str, expected: int) -> tuple[str, ...]:
    """Decode an opaque cursor and reject an invalid component count."""
    parts: tuple[str, ...] = decode_cursor(cursor)
    if len(parts) != expected:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
    return parts


def _cursor_datetime(value: str) -> datetime:
    """Parse a cursor timestamp without exposing malformed input."""
    try:
        return ensure_utc(datetime.fromisoformat(value))
    except ValueError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
