"""Database model metadata and core constraint tests."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Boolean, DateTime, Float, Integer, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from telescreen.db.base import Base
from telescreen.db.models import AuditChainHeadRow, CounterRow, FindingRow
from tests.support.db import SqliteHarness, make_sqlite_harness

EXPECTED_TABLES: set[str] = {
    "members",
    "teams",
    "team_memberships",
    "api_tokens",
    "counters",
    "collector_configs",
    "scan_jobs",
    "replay_nonces",
    "issues",
    "findings",
    "issue_history",
    "workflow_definitions",
    "escalation_policies",
    "workflow_runs",
    "step_runs",
    "transitions",
    "engine_tasks",
    "human_tasks",
    "map_jobs",
    "map_partitions",
    "map_items",
    "notifications",
    "evidence",
    "police_workers",
    "police_tasks",
    "audit_records",
    "audit_chain_head",
    # v1.1 (design-v1-1-dspm-adoption.md)
    "placement_policies",
    "resource_coverage",
    "scan_coverage",
    "collector_health",
    "member_identities",
    "issue_exceptions",
    "notification_templates",
    # v1.3 (design-v1-3-sentry-parity.md)
    "saved_views",
    "saved_view_stars",
    "saved_view_defaults",
    "issue_comments",
    "issue_mentions",
    "issue_subscriptions",
    "issue_bookmarks",
    "external_issue_links",
    "issue_merges",
    "ownership_rules",
    "alert_rules",
    "alert_fires",
    "notification_settings",
    "integration_checks",
    # v1.4 data inventory
    "data_assets",
    "data_asset_events",
    "data_asset_items",
    "data_fields",
    "data_field_values",
}


def test_metadata_has_the_exact_table_contract() -> None:
    """Expose every planned table in the ORM metadata."""
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert len(Base.metadata.tables) == 53


@pytest.mark.asyncio
async def test_every_table_accepts_a_minimal_row() -> None:
    """Insert one type-correct row into every table using SQLite."""
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            column_values: list[tuple[str, str | int | float | bool | datetime]] = []
            for column in table.columns:
                if column.nullable or column.default is not None:
                    continue
                if isinstance(column.type, DateTime):
                    column_value: str | int | float | bool | datetime = datetime(
                        2026, 10, 8, tzinfo=UTC
                    )
                elif isinstance(column.type, Boolean):
                    column_value = True
                elif isinstance(column.type, Integer):
                    column_value = 1
                elif isinstance(column.type, Float):
                    column_value = 1.0
                else:
                    column_value = f"{table.name}-{column.name}"
                column_values.append((column.name, column_value))
            await connection.execute(insert(table).values(dict(column_values)))
            row_count: int | None = await connection.scalar(select(func.count()).select_from(table))
            assert row_count == 1, table.name
    await engine.dispose()


@pytest.mark.asyncio
async def test_harness_seeds_shared_rows_and_finding_uniqueness() -> None:
    """Create the schema, seed shared rows and enforce finding de-duplication."""
    harness: SqliteHarness = await make_sqlite_harness()
    at: datetime = datetime(2026, 10, 8, tzinfo=UTC)
    async with harness.sessions.begin() as session:
        counter: CounterRow | None = await session.get(CounterRow, "issue_number")
        head: AuditChainHeadRow | None = await session.get(AuditChainHeadRow, 1)
        assert counter is not None and counter.value == 0
        assert head is not None and head.seq == 0 and head.hash == "0" * 64
        session.add(
            FindingRow(
                id="finding-1",
                issue_id="issue-1",
                scan_job_id="scan-1",
                source="github",
                resource_key="a" * 64,
                display_locator="repo/file",
                location="line 1",
                rule_id="credential.test",
                rule_class="credential",
                detector="rule",
                confidence=1.0,
                masked_snippet="[REDACTED]",
                fingerprint="b" * 64,
                fingerprint_key_id="key-1",
                observed_at=at,
            )
        )
    async with harness.sessions.begin() as session:
        session.add(
            FindingRow(
                id="finding-2",
                issue_id="issue-1",
                scan_job_id="scan-1",
                source="github",
                resource_key="a" * 64,
                display_locator="repo/file",
                location="line 1",
                rule_id="credential.test",
                rule_class="credential",
                detector="rule",
                confidence=1.0,
                masked_snippet="[REDACTED]",
                fingerprint="b" * 64,
                fingerprint_key_id="key-1",
                observed_at=at,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
    await harness.close()
