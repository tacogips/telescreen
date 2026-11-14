"""Meilisearch filterable and sortable attribute configuration."""

from dataclasses import dataclass

from telescreen.search.backend import IndexName


@dataclass(frozen=True)
class IndexSettings:
    """Search settings for one index."""

    filterable: tuple[str, ...]
    sortable: tuple[str, ...]


INDEX_SETTINGS: tuple[tuple[IndexName, IndexSettings], ...] = (
    (
        IndexName.ISSUES,
        IndexSettings(
            ("status", "source", "rule_class", "severity", "assignee"),
            ("last_seen_ts", "first_seen_ts", "times_seen"),
        ),
    ),
    (IndexName.FINDINGS, IndexSettings(("issue_id", "rule_class"), ())),
    (IndexName.RUNS, IndexSettings(("status", "workflow", "issue_id"), ())),
    (
        IndexName.AUDIT,
        IndexSettings(("action", "actor_ref", "target_kind", "at_ts"), ("seq", "at_ts")),
    ),
)
