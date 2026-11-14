"""Database queries and durable mutations for map jobs."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonKind, JsonValue, parse_json
from telescreen.core.vocabulary import MapItemStatus
from telescreen.db.models import MapItemRow
from telescreen.ports.sweeps import RuleClassCount, ShardSummary


async def status_counts(session: AsyncSession, job_id: str) -> tuple[tuple[str, int], ...]:
    """Return item counts grouped by durable state."""
    rows: list[tuple[str, int]] = list(
        (
            await session.execute(
                select(MapItemRow.status, func.count())
                .where(MapItemRow.map_job_id == job_id)
                .group_by(MapItemRow.status)
            )
        )
        .tuples()
        .all()
    )
    return tuple(rows)


async def in_flight_count(session: AsyncSession, job_id: str) -> int:
    """Count dispatched shards from database state."""
    count: int | None = await session.scalar(
        select(func.count())
        .select_from(MapItemRow)
        .where(
            MapItemRow.map_job_id == job_id,
            MapItemRow.status == MapItemStatus.DISPATCHED.value,
        )
    )
    return count or 0


async def aggregate_summaries(session: AsyncSession, job_id: str) -> ShardSummary:
    """Sum count-only shard summaries without loading resource data."""
    values: tuple[str | None, ...] = tuple(
        await session.scalars(
            select(MapItemRow.summary_json).where(
                MapItemRow.map_job_id == job_id,
                MapItemRow.status == MapItemStatus.SUCCEEDED.value,
            )
        )
    )
    total: ShardSummary = ShardSummary.empty()
    for encoded in values:
        if encoded is not None:
            total = total.plus(summary_from_json(parse_json(encoded)))
    return total


def summary_to_json(summary: ShardSummary) -> JsonValue:
    """Encode a shard summary as count-only JSON."""
    from telescreen.core.json import json_array, json_int, json_object, json_str

    return json_object(
        (
            ("resources_scanned", json_int(summary.resources_scanned)),
            ("resources_skipped", json_int(summary.resources_skipped)),
            ("resources_failed", json_int(summary.resources_failed)),
            ("bytes_scanned", json_int(summary.bytes_scanned)),
            ("findings_inserted", json_int(summary.findings_inserted)),
            ("issues_opened", json_int(summary.issues_opened)),
            ("issues_auto_resolved", json_int(summary.issues_auto_resolved)),
            (
                "findings_by_rule_class",
                json_array(
                    tuple(
                        json_object(
                            (
                                ("rule_class", json_str(item.rule_class.value)),
                                ("count", json_int(item.count)),
                            )
                        )
                        for item in summary.findings_by_rule_class
                    )
                ),
            ),
        )
    )


def summary_from_json(value: JsonValue) -> ShardSummary:
    """Decode stored summary JSON defensively to counters only."""
    from telescreen.core.vocabulary import RuleClass

    def number(name: str) -> int:
        field: JsonValue | None = value.get(name)
        result: int | None = field.as_int() if field is not None else None
        return result if result is not None else 0

    rules: list[RuleClassCount] = []
    values: JsonValue | None = value.get("findings_by_rule_class")
    if values is not None and values.kind is JsonKind.ARRAY:
        for entry in values.items:
            name: JsonValue | None = entry.get("rule_class")
            count: JsonValue | None = entry.get("count")
            try:
                if name is not None and name.as_str() is not None and count is not None:
                    rules.append(
                        RuleClassCount(RuleClass(name.as_str() or ""), count.as_int() or 0)
                    )
            except ValueError:
                continue
    return ShardSummary(
        number("resources_scanned"),
        number("resources_skipped"),
        number("resources_failed"),
        number("bytes_scanned"),
        number("findings_inserted"),
        number("issues_opened"),
        number("issues_auto_resolved"),
        tuple(rules),
    )
