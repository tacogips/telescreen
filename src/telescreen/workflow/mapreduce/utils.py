"""Safe conversions for persisted map-reduce specifications."""

from datetime import timedelta

from telescreen.core.json import JsonKind, JsonValue, parse_json
from telescreen.workflow.dsl.model import MapVia
from telescreen.workflow.mapreduce.spec import MapJobSpec


def spec_from_json(encoded: str) -> MapJobSpec:
    """Decode a persisted job spec using safe defaults for malformed fields."""
    value: JsonValue = parse_json(encoded)

    def string(name: str) -> str:
        member: JsonValue | None = value.get(name)
        if member is None:
            return ""
        result: str | None = member.as_str()
        return result if result is not None else ""

    def integer(name: str, default: int) -> int:
        member: JsonValue | None = value.get(name)
        if member is None:
            return default
        result: int | None = member.as_int()
        return result if result is not None else default

    def number(name: str, default: float) -> float:
        member: JsonValue | None = value.get(name)
        if member is None:
            return default
        result: float | None = member.as_float()
        return result if result is not None else default

    partition_via: MapVia = MapVia(string("partition_via") or "collector")
    map_via: MapVia = MapVia(string("map_via") or "collector")
    return MapJobSpec(
        partition_via,
        map_via,
        string("partition_task_type") or None,
        string("map_task_type") or None,
        labels_from_json(value.get("partition_labels")),
        labels_from_json(value.get("map_labels")),
        integer("max_concurrency", 16),
        timedelta(seconds=number("shard_timeout_seconds", 900)),
        integer("shard_max_attempts", 3),
        number("max_failed_ratio", 0.01),
        integer("max_failed_items", 100),
        timedelta(seconds=number("timeout_seconds", 86400)),
    )


def labels_from_json(value: JsonValue | None) -> tuple[str, ...]:
    """Read legacy object or array encoded string labels."""
    if value is None:
        return ()
    members: tuple[JsonValue, ...] = (
        value.items if value.kind is JsonKind.ARRAY else tuple(item.value for item in value.members)
    )
    return tuple(text for item in members if (text := item.as_str()) is not None)
