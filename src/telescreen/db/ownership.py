"""Explicit persistence ownership for the modular monolith and store migrations."""

from enum import StrEnum


class Store(StrEnum):
    """Independent persistence units that may share one URL in the legacy deployment."""

    MAIN = "main"
    INVENTORY = "inventory"


INVENTORY_TABLES: tuple[str, ...] = (
    "data_assets",
    "data_asset_events",
    "data_asset_items",
    "data_fields",
    "data_field_values",
    "inventory_counters",
    "inventory_inbox_streams",
    "inventory_inbox",
    "inventory_audit_outbox",
    "inventory_retention_outbox",
)


MAIN_TABLES: tuple[str, ...] = (
    "alert_fires",
    "alert_rules",
    "api_tokens",
    "audit_chain_head",
    "audit_receipts",
    "audit_records",
    "collector_configs",
    "collector_health",
    "counters",
    "engine_tasks",
    "escalation_policies",
    "evidence",
    "external_issue_links",
    "findings",
    "human_tasks",
    "integration_checks",
    "inventory_outbox",
    "inventory_outbox_streams",
    "issue_bookmarks",
    "issue_comments",
    "issue_exceptions",
    "issue_history",
    "issue_mentions",
    "issue_merges",
    "issue_subscriptions",
    "issues",
    "map_items",
    "map_jobs",
    "map_partitions",
    "member_identities",
    "members",
    "notification_settings",
    "notification_templates",
    "notifications",
    "ownership_rules",
    "placement_policies",
    "police_tasks",
    "police_workers",
    "replay_nonces",
    "resource_coverage",
    "retention_notification_receipts",
    "saved_view_defaults",
    "saved_view_stars",
    "saved_views",
    "scan_coverage",
    "scan_jobs",
    "step_runs",
    "team_memberships",
    "teams",
    "transitions",
    "workflow_definitions",
    "workflow_runs",
)


def tables_for(store: Store) -> tuple[str, ...]:
    """Return the exact table contract for a store, without querying another database."""
    return INVENTORY_TABLES if store is Store.INVENTORY else MAIN_TABLES


def store_for(table_name: str) -> Store:
    """Fail on unknown tables so schema additions require an ownership decision."""
    if table_name in INVENTORY_TABLES:
        return Store.INVENTORY
    if table_name in MAIN_TABLES:
        return Store.MAIN
    raise ValueError("table has no declared persistence owner")
