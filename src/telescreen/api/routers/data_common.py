"""Shared data inventory API response serialization and safe query parsing."""

from datetime import datetime

from starlette.datastructures import QueryParams

from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonValue,
    json_array,
    json_bool,
    json_int,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.vocabulary import ErrorCode
from telescreen.inventory.queries import (
    AssetDetailView,
    AssetFilters,
    AssetView,
    EventView,
    FieldFilter,
    ItemView,
)
from telescreen.masking.redact import Redactor


def asset_json(view: AssetView) -> JsonValue:
    """Serialize an asset list row using the v1.4 response contract."""
    return json_object(
        (
            ("id", json_str(view.id)),
            ("short_id", json_str(view.short_id)),
            ("number", json_int(view.number)),
            ("name", json_str(view.name)),
            ("source", json_str(view.source)),
            ("display_locator", json_str(view.display_locator)),
            ("kind", json_str(view.kind)),
            ("origin", json_str(view.origin)),
            ("state", json_str(view.state)),
            ("item_count", _optional_int(view.item_count)),
            ("item_trend", json_array(tuple(json_int(value) for value in view.item_trend))),
            ("open_issue_count", json_int(view.open_issue_count)),
            (
                "owner",
                json_object(
                    (
                        ("ref", json_str(view.owner.ref)),
                        ("display_name", json_str(view.owner.display_name)),
                    )
                )
                if view.owner
                else json_null(),
            ),
            ("deletion_due", _optional_str(view.deletion_due)),
            ("overdue", json_bool(view.overdue)),
            ("incomplete", json_bool(view.incomplete)),
            ("first_seen_at", json_str(_time(view.first_seen_at))),
            ("last_seen_at", _optional_time(view.last_seen_at)),
            ("deleted_at", _optional_time(view.deleted_at)),
            ("collector_config_id", _optional_str(view.collector_config_id)),
        )
    )


def asset_detail_json(view: AssetDetailView) -> JsonValue:
    """Serialize detail fields layered over the list asset shape."""
    base: tuple[tuple[str, JsonValue], ...] = (
        ("sweep_covered", json_bool(view.sweep_covered)),
        (
            "created_by",
            json_object(
                (
                    ("kind", json_str(view.created_by.kind)),
                    ("ref", json_str(view.created_by.ref)),
                    ("display_name", json_str(view.created_by.display_name)),
                )
            )
            if view.created_by
            else json_null(),
        ),
        ("value_count", json_int(view.value_count)),
        (
            "fields",
            json_array(
                tuple(
                    json_object(
                        (
                            ("key", json_str(field.key)),
                            ("label", json_str(field.label)),
                            ("field_type", json_str(field.field_type)),
                            ("required", json_bool(field.required)),
                            ("role", _optional_str(field.role)),
                            (
                                "options",
                                field.options if field.options is not None else json_null(),
                            ),
                            ("value_json", _optional_str(field.value_json)),
                        )
                    )
                    for field in view.fields
                )
            ),
        ),
        ("missing_required", json_array(tuple(json_str(key) for key in view.missing_required))),
        (
            "open_issues",
            json_array(
                tuple(
                    json_object(
                        (
                            ("issue_id", json_str(issue.issue_id)),
                            ("short_id", json_str(issue.short_id)),
                        )
                    )
                    for issue in view.open_issues
                )
            ),
        ),
    )
    list_json: JsonValue = asset_json(view.asset)
    members: list[tuple[str, JsonValue]] = [
        (member.name, member.value) for member in list_json.members
    ]
    members.extend(base)
    return json_object(tuple(members))


def event_json(view: EventView) -> JsonValue:
    """Serialize one append-only asset event."""
    return json_object(
        (
            ("id", json_str(view.id)),
            ("kind", json_str(view.kind)),
            ("occurred_at", _optional_time(view.occurred_at)),
            ("recorded_at", json_str(_time(view.recorded_at))),
            (
                "actor",
                json_object(
                    (
                        ("kind", json_str(view.actor.kind)),
                        ("ref", json_str(view.actor.ref)),
                        ("display_name", json_str(view.actor.display_name)),
                    )
                ),
            ),
            ("items_added", _optional_int(view.items_added)),
            ("items_removed", _optional_int(view.items_removed)),
            ("item_count_after", _optional_int(view.item_count_after)),
            ("values_added", _optional_int(view.values_added)),
            ("values_removed", _optional_int(view.values_removed)),
            ("value_count_after", _optional_int(view.value_count_after)),
            ("field_key", _optional_str(view.field_key)),
            ("old_value", _optional_str(view.old_value)),
            ("new_value", _optional_str(view.new_value)),
            ("note", _optional_str(view.note)),
        )
    )


def item_json(view: ItemView) -> JsonValue:
    """Serialize an item without exposing its full fingerprint."""
    return json_object(
        (
            ("id", json_str(view.id)),
            ("kind", json_str(view.kind)),
            ("key_prefix", json_str(view.key_prefix)),
            ("first_seen_at", json_str(_time(view.first_seen_at))),
            ("removed_at", _optional_time(view.removed_at)),
        )
    )


def parse_filters(params: QueryParams, redactor: Redactor) -> AssetFilters:
    """Parse list query parameters and reject secrets without echoing them."""
    q: str | None = params.get("q")
    if q is not None:
        if len(q) > 200:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid query")
        if redactor.has_hits(q):
            raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "sensitive query rejected")
    fields_raw: list[str] = params.getlist("field")
    if len(fields_raw) > 5:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "too many field filters")
    fields: list[FieldFilter] = []
    for entry in fields_raw:
        key: str
        separator: str
        value: str
        key, separator, value = entry.partition(":")
        if not separator or not key or not value:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid field filter")
        if redactor.has_hits(value):
            raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "sensitive query rejected")
        fields.append(FieldFilter(key, value))
    tab: str = params.get("tab", "all") or "all"
    sort: str = params.get("sort", "last_seen") or "last_seen"
    if tab not in ("all", "present", "deleted", "incomplete", "overdue"):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid tab")
    if sort.lstrip("-") not in (
        "last_seen",
        "first_seen",
        "name",
        "item_count",
        "deletion_due",
        "short_id",
    ):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid sort")
    try:
        limit: int = int(params.get("limit", "50") or "50")
    except ValueError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid limit") from None
    if not 1 <= limit <= 200:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid limit")
    return AssetFilters(
        tab=tab,
        q=q,
        source=params.get("source"),
        kind=params.get("kind"),
        state=params.get("state"),
        origin=params.get("origin"),
        owner=params.get("owner"),
        collector_config_id=params.get("collector_config_id"),
        fields=tuple(fields),
        sort=sort,
        limit=limit,
        cursor=params.get("cursor"),
    )


def _time(value: datetime) -> str:
    """Format a timestamp as ISO-8601."""
    return value.isoformat()


def _optional_time(value: datetime | None) -> JsonValue:
    """Serialize an optional timestamp."""
    return json_str(_time(value)) if value is not None else json_null()


def _optional_str(value: str | None) -> JsonValue:
    """Serialize an optional string."""
    return json_str(value) if value is not None else json_null()


def _optional_int(value: int | None) -> JsonValue:
    """Serialize an optional integer."""
    return json_int(value) if value is not None else json_null()
