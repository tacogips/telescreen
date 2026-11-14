"""Regression tests for the idempotent v1.1 schema migration."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, Inspector

from telescreen.db.base import Base
from telescreen.db.migrate import upgrade
from telescreen.db.models import IssueRow

V11_TABLES: set[str] = {
    "placement_policies",
    "issue_exceptions",
    "member_identities",
    "resource_coverage",
    "scan_coverage",
    "collector_health",
}
V11_COLUMNS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "issues",
        frozenset(
            {
                "base_severity",
                "exposure_visibility",
                "exposure_json",
                "exposure_changed_at",
                "placement_keys_json",
                "resource_modified_at",
                "owner_ref",
                "policy_name",
                "policy_version",
                "risk_score",
                "asset_tier",
            }
        ),
    ),
    ("findings", frozenset({"fingerprint_key_version"})),
    ("teams", frozenset({"lead_member_id"})),
    ("police_workers", frozenset({"health_alerted_at"})),
)


def _alembic_config(database_url: str) -> Config:
    """Build an Alembic config matching the programmatic migration entry point."""
    migration_directory: Path = Path(__file__).parents[2] / "src/telescreen/db/migrations"
    config: Config = Config()
    config.set_main_option("script_location", migration_directory.as_posix())
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _assert_v11_schema(engine: Engine) -> None:
    """Assert the v1.1 tables and columns are present in the live schema."""
    inspector: Inspector = inspect(engine)
    table_names: set[str] = set(inspector.get_table_names())
    assert table_names >= V11_TABLES
    for table_name, expected_columns in V11_COLUMNS:
        actual_columns: set[str] = {column["name"] for column in inspector.get_columns(table_name)}
        assert expected_columns <= actual_columns
    index_names: set[str] = {
        str(index["name"])
        for table_name in V11_TABLES | {"issues", "findings"}
        for index in inspector.get_indexes(table_name)
        if index["name"] is not None
    }
    assert {
        "ix_issues_status_risk",
        "ix_issues_exposure_visibility",
        "ix_findings_key_fingerprint",
        "ix_issue_exceptions_issue",
        "ix_issue_exceptions_status_expiry",
        "ix_member_identities_member_id",
    } <= index_names


def _remove_v11_schema(engine: Engine) -> None:
    """Turn a current metadata schema into the historical v1 schema shape."""
    with engine.begin() as connection:
        for table_name, column_names in V11_COLUMNS:
            for index in inspect(connection).get_indexes(table_name):
                if index["name"] in {
                    "ix_issues_status_risk",
                    "ix_issues_exposure_visibility",
                    "ix_findings_key_fingerprint",
                }:
                    connection.execute(text(f'DROP INDEX "{index["name"]}"'))
            existing_columns: set[str] = {
                column["name"] for column in inspect(connection).get_columns(table_name)
            }
            for column_name in column_names:
                if column_name in existing_columns:
                    connection.execute(
                        text(f'ALTER TABLE "{table_name}" DROP COLUMN "{column_name}"')
                    )
        for table_name in V11_TABLES:
            connection.execute(text(f'DROP TABLE "{table_name}"'))


def test_fresh_schema_has_v11_tables_and_columns(tmp_path: Path) -> None:
    """Upgrade a fresh database through 0001 and idempotent 0002."""
    database_file: Path = tmp_path / "fresh-v11.sqlite"
    database_url: str = f"sqlite+aiosqlite:///{database_file}"
    asyncio.run(asyncio.to_thread(upgrade, database_url))
    engine: Engine = create_engine(f"sqlite:///{database_file}")
    _assert_v11_schema(engine)
    assert inspect(engine).get_table_names().count("alembic_version") == 1
    engine.dispose()


def test_v1_schema_upgrade_adds_columns_and_backfills(tmp_path: Path) -> None:
    """Upgrade a stamped v1 database and preserve its existing issue severity."""
    database_file: Path = tmp_path / "existing-v1.sqlite"
    sync_url: str = f"sqlite:///{database_file}"
    async_url: str = f"sqlite+aiosqlite:///{database_file}"
    engine: Engine = create_engine(sync_url)
    Base.metadata.create_all(engine)
    _remove_v11_schema(engine)
    observed_at: str = datetime(2026, 10, 10, tzinfo=UTC).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO issues (id, number, grouping_key, source, resource_key, "
                "display_locator, display_title, rule_class, severity, status, first_seen, "
                "last_seen, last_checked_at, sweep_covered, times_seen, distinct_fingerprints, "
                "regression_count, created_at, updated_at) VALUES "
                "('issue-1', 1, 'group-1', 'github', 'resource-1', 'masked', 'masked', "
                "'credential', 'high', 'open', :observed_at, :observed_at, :observed_at, "
                "0, 1, 1, 0, :observed_at, :observed_at)"
            ),
            {"observed_at": observed_at},
        )
        connection.execute(
            text(
                "INSERT INTO findings (id, issue_id, source, resource_key, display_locator, "
                "location, rule_id, rule_class, detector, confidence, masked_snippet, "
                "fingerprint, fingerprint_key_id, observed_at) VALUES "
                "('finding-1', 'issue-1', 'github', 'resource-1', 'masked', 'line 1', "
                "'rule', 'credential', 'rule', 1.0, '[REDACTED]', 'fingerprint', 'key-1', "
                ":observed_at)"
            ),
            {"observed_at": observed_at},
        )
    config: Config = _alembic_config(async_url)
    command.stamp(config, "0001_initial")
    asyncio.run(asyncio.to_thread(upgrade, async_url))
    _assert_v11_schema(engine)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT base_severity FROM issues WHERE id = 'issue-1'"))
            == "high"
        )
        assert connection.scalar(text("SELECT risk_score FROM issues WHERE id = 'issue-1'")) == 0
        assert (
            connection.scalar(text("SELECT exposure_visibility FROM issues WHERE id = 'issue-1'"))
            == "unknown"
        )
        assert (
            connection.scalar(
                text("SELECT fingerprint_key_version FROM findings WHERE id = 'finding-1'")
            )
            == 1
        )
    engine.dispose()


def test_downgrade_and_upgrade_remove_and_restore_v11_tables(tmp_path: Path) -> None:
    """Downgrade removes v1.1 additions, and a subsequent upgrade restores them."""
    database_file: Path = tmp_path / "round-trip.sqlite"
    database_url: str = f"sqlite+aiosqlite:///{database_file}"
    config: Config = _alembic_config(database_url)
    asyncio.run(asyncio.to_thread(upgrade, database_url))
    command.downgrade(config, "0001_initial")
    engine: Engine = create_engine(f"sqlite:///{database_file}")
    table_names: set[str] = set(inspect(engine).get_table_names())
    assert not V11_TABLES.intersection(table_names)
    for table_name, expected_columns in V11_COLUMNS:
        actual_columns: set[str] = {
            column["name"] for column in inspect(engine).get_columns(table_name)
        }
        assert not expected_columns.intersection(actual_columns)
    asyncio.run(asyncio.to_thread(upgrade, database_url))
    _assert_v11_schema(engine)
    engine.dispose()


def test_issue_model_defaults_keep_v1_constructors_compatible() -> None:
    """New issue metadata defaults preserve construction with only v1 fields."""
    observed_at: datetime = datetime(2026, 10, 10, tzinfo=UTC)
    issue: IssueRow = IssueRow(
        id="issue-1",
        number=1,
        grouping_key="group-1",
        source="github",
        resource_key="resource-1",
        display_locator="masked",
        display_title="masked",
        rule_class="credential",
        severity="medium",
        status="open",
        first_seen=observed_at,
        last_seen=observed_at,
        last_checked_at=observed_at,
        created_at=observed_at,
        updated_at=observed_at,
    )
    assert issue.base_severity == "medium"
    assert issue.risk_score == 0
    assert issue.exposure_visibility == "unknown"
