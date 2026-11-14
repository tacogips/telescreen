"""Safe JSON encoding for collector selectors and cursors."""

from dataclasses import dataclass
from datetime import UTC, datetime

from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.vocabulary import CollectorKind, ErrorCode
from telescreen.ports.collectors import (
    CollectorSelector,
    DriveSelector,
    GitHubSelector,
    PoliceSelector,
    SlackSelector,
    WatchState,
)


@dataclass(frozen=True)
class CursorState:
    """Stored incremental cursor and watch state for one collector."""

    cursor: str | None = None
    watch: WatchState | None = None
    last_sweep_fire: datetime | None = None


def selector_to_json(selector: CollectorSelector) -> JsonValue:
    """Encode a typed selector as a closed JSON value."""
    if isinstance(selector, SlackSelector):
        return json_object(
            (
                ("channel_ids", _strings(selector.channel_ids)),
                ("include_files", json_bool(selector.include_files)),
            )
        )
    if isinstance(selector, DriveSelector):
        return json_object(
            (
                ("drive_ids", _strings(selector.drive_ids)),
                ("folder_ids", _strings(selector.folder_ids)),
                ("mime_types", _strings(selector.mime_types)),
            )
        )
    if isinstance(selector, GitHubSelector):
        return json_object(
            (
                ("owner", json_str(selector.owner)),
                ("repos", _strings(selector.repos)),
                ("branches", _strings(selector.branches)),
                ("path_globs", _strings(selector.path_globs)),
            )
        )
    if isinstance(selector, PoliceSelector):
        return json_object((("labels", _strings(selector.labels)), ("input", selector.input)))
    return selector.document


def selector_from_json(kind: CollectorKind, value: JsonValue) -> CollectorSelector:
    """Decode a selector, mapping any malformed shape to a static validation error."""
    if value.kind is not JsonKind.OBJECT:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid collector selector")
    try:
        if kind is CollectorKind.SLACK:
            return SlackSelector(
                _read_strings(value, "channel_ids"), _read_bool(value, "include_files")
            )
        if kind in (CollectorKind.GDRIVE, CollectorKind.GDOCS, CollectorKind.GSHEETS):
            return DriveSelector(
                _read_strings(value, "drive_ids"),
                _read_strings(value, "folder_ids"),
                _read_strings(value, "mime_types"),
            )
        if kind is CollectorKind.GITHUB:
            owner: str = _read_string(value, "owner")
            return GitHubSelector(
                owner,
                _read_strings(value, "repos"),
                _read_strings(value, "branches"),
                _read_strings(value, "path_globs"),
            )
        if kind is CollectorKind.POLICE:
            input_value: JsonValue | None = value.get("input")
            if input_value is None:
                raise ValueError
            return PoliceSelector(_read_strings(value, "labels"), input_value)
        from telescreen.ports.collectors import CustomSelector

        return CustomSelector(value)
    except (ValueError, TypeError):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid collector selector") from None


def cursor_to_json(state: CursorState) -> str:
    """Serialize cursor and watch fields in stable JSON form."""
    watch_value: JsonValue = (
        json_object(
            (
                ("channel_id", json_str(state.watch.channel_id)),
                ("resource_id", json_str(state.watch.resource_id)),
                ("expires_at", json_str(state.watch.expires_at.astimezone(UTC).isoformat())),
            )
        )
        if state.watch is not None
        else parse_json("null")
    )
    sweep_value: JsonValue = (
        json_str(state.last_sweep_fire.astimezone(UTC).isoformat())
        if state.last_sweep_fire is not None
        else parse_json("null")
    )
    return dump_json(
        json_object(
            (
                (
                    "cursor",
                    json_str(state.cursor) if state.cursor is not None else parse_json("null"),
                ),
                ("watch", watch_value),
                ("last_sweep_fire", sweep_value),
            )
        )
    )


def cursor_from_json(text: str | None) -> CursorState:
    """Parse stored cursor JSON without echoing malformed values."""
    if text is None:
        return CursorState()
    try:
        value: JsonValue = parse_json(text)
        cursor_value: JsonValue | None = value.get("cursor")
        watch_value: JsonValue | None = value.get("watch")
        sweep_value: JsonValue | None = value.get("last_sweep_fire")
        watch: WatchState | None = None
        if watch_value is not None and not watch_value.is_null():
            channel_id: str = _read_string(watch_value, "channel_id")
            resource_id: str = _read_string(watch_value, "resource_id")
            expires_at: datetime = _parse_datetime(_read_string(watch_value, "expires_at"))
            watch = WatchState(channel_id, resource_id, expires_at)
        sweep: datetime | None = (
            None
            if sweep_value is None or sweep_value.is_null()
            else _parse_datetime(_string_value(sweep_value))
        )
        cursor: str | None = (
            None if cursor_value is None or cursor_value.is_null() else _string_value(cursor_value)
        )
        return CursorState(cursor, watch, sweep)
    except (ValueError, TypeError):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid collector cursor") from None


def _strings(values: tuple[str, ...]) -> JsonValue:
    """Build a JSON string array."""
    return json_array(tuple(json_str(value) for value in values))


def _read_strings(value: JsonValue, name: str) -> tuple[str, ...]:
    """Read an array of strings from an object."""
    child: JsonValue | None = value.get(name)
    if child is None or child.as_list() is None:
        raise ValueError
    values: tuple[JsonValue, ...] = child.items
    if any(item.as_str() is None for item in values):
        raise ValueError
    return tuple(item.str_value for item in values)


def _read_string(value: JsonValue, name: str) -> str:
    """Read one required string member."""
    child: JsonValue | None = value.get(name)
    if child is None:
        raise ValueError
    return _string_value(child)


def _string_value(value: JsonValue) -> str:
    """Read a JSON string value."""
    result: str | None = value.as_str()
    if result is None:
        raise ValueError
    return result


def _read_bool(value: JsonValue, name: str) -> bool:
    """Read one required boolean member."""
    child: JsonValue | None = value.get(name)
    result: bool | None = child.as_bool() if child is not None else None
    if result is None:
        raise ValueError
    return result


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO timestamp and normalize it to UTC."""
    parsed: datetime = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
