"""Regression tests for the idempotent v1.3 schema migration."""

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, Inspector
from sqlalchemy.orm import Session

from telescreen.db.models import (
    AlertFireRow,
    AlertRuleRow,
    ExternalIssueLinkRow,
    IntegrationCheckRow,
    IssueBookmarkRow,
    IssueCommentRow,
    IssueMentionRow,
    IssueMergeRow,
    IssueSubscriptionRow,
    NotificationSettingRow,
    OwnershipRuleRow,
    SavedViewDefaultRow,
    SavedViewRow,
    SavedViewStarRow,
)

V13_TABLES: tuple[str, ...] = (
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
)
ISSUE_COLUMNS: frozenset[str] = frozenset(
    {
        "priority",
        "priority_manual",
        "needs_review",
        "merged_into_id",
        "archive_kind",
        "archive_until",
        "archive_threshold",
        "archive_baseline",
        "archived_at",
    }
)
AT: datetime = datetime(2026, 10, 12, tzinfo=UTC)


def _alembic_config(database_url: str) -> Config:
    """Build an Alembic config for a temporary SQLite database."""
    migration_directory: Path = Path(__file__).parents[2] / "src/telescreen/db/migrations"
    config: Config = Config()
    config.set_main_option("script_location", migration_directory.as_posix())
    async_database_url: str = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    config.set_main_option("sqlalchemy.url", async_database_url.replace("%", "%%"))
    return config


def _assert_v13_schema(engine: Engine) -> None:
    """Assert the v1.3 tables, columns and issue indexes are present."""
    inspector: Inspector = inspect(engine)
    table_names: set[str] = set(inspector.get_table_names())
    assert set(V13_TABLES) <= table_names
    issue_columns: set[str] = {column["name"] for column in inspector.get_columns("issues")}
    notification_columns: set[str] = {
        column["name"] for column in inspector.get_columns("notifications")
    }
    assert issue_columns >= ISSUE_COLUMNS
    assert "category" in notification_columns
    issue_indexes: set[str] = {
        str(index["name"]) for index in inspector.get_indexes("issues") if index["name"] is not None
    }
    assert {"ix_issues_status_merged", "ix_issues_merged_into_id"} <= issue_indexes


def _reconstruct_v12_schema(engine: Engine) -> None:
    """Remove v1.3 schema additions and mark the database as revision 0003."""
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_issues_status_merged"))
        connection.execute(text("DROP INDEX ix_issues_merged_into_id"))
        for column_name in (
            "archived_at",
            "archive_baseline",
            "archive_threshold",
            "archive_until",
            "archive_kind",
            "merged_into_id",
            "needs_review",
            "priority_manual",
            "priority",
        ):
            connection.execute(text(f"ALTER TABLE issues DROP COLUMN {column_name}"))
        connection.execute(text("ALTER TABLE notifications DROP COLUMN category"))
        for table_name in V13_TABLES:
            connection.execute(text(f"DROP TABLE {table_name}"))
        connection.execute(text("UPDATE alembic_version SET version_num = '0003_v1_2'"))


def _seed_legacy_issues(engine: Engine) -> None:
    """Insert legacy issue rows with varied severity and lifecycle state."""
    with engine.begin() as connection:
        for issue_id, severity, status, number in (
            ("issue-critical", "critical", "unresolved", 101),
            ("issue-medium", "medium", "regressed", 102),
            ("issue-resolved", "high", "resolved", 103),
        ):
            connection.execute(
                text(
                    "INSERT INTO issues (id, number, grouping_key, source, resource_key, "
                    "display_locator, display_title, rule_class, severity, status, first_seen, "
                    "last_seen, last_checked_at, sweep_covered, times_seen, "
                    "distinct_fingerprints, regression_count, created_at, updated_at) "
                    "VALUES (:id, :number, :grouping_key, 'github', 'resource', 'repo/file', "
                    "'Masked title', 'credential', :severity, :status, :at, :at, :at, 0, 1, "
                    "1, 0, :at, :at)"
                ),
                {
                    "id": issue_id,
                    "number": number,
                    "grouping_key": issue_id,
                    "severity": severity,
                    "status": status,
                    "at": AT.isoformat(),
                },
            )


def test_fresh_schema_has_v13_additions_and_upgrade_is_idempotent(tmp_path: Path) -> None:
    """Upgrade a fresh database and safely apply the head migration again."""
    database_file: Path = tmp_path / "fresh-v13.sqlite"
    database_url: str = f"sqlite:///{database_file}"
    config: Config = _alembic_config(database_url)
    command.upgrade(config, "head")
    engine: Engine = create_engine(database_url)
    _assert_v13_schema(engine)
    command.upgrade(config, "head")
    _assert_v13_schema(engine)
    engine.dispose()


def test_v12_schema_backfills_priority_and_review_and_downgrades(tmp_path: Path) -> None:
    """Backfill only legacy issues, preserve rows, and remove v1.3 on downgrade."""
    database_file: Path = tmp_path / "legacy-v12.sqlite"
    database_url: str = f"sqlite:///{database_file}"
    config: Config = _alembic_config(database_url)
    command.upgrade(config, "head")
    engine: Engine = create_engine(database_url)
    _reconstruct_v12_schema(engine)
    _seed_legacy_issues(engine)

    command.upgrade(config, "head")
    _assert_v13_schema(engine)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT priority, needs_review FROM issues WHERE id = 'issue-critical'")
        ).one() == ("high", 1)
        assert connection.execute(
            text("SELECT priority, needs_review FROM issues WHERE id = 'issue-medium'")
        ).one() == ("medium", 1)
        assert connection.execute(
            text("SELECT priority, needs_review FROM issues WHERE id = 'issue-resolved'")
        ).one() == ("high", 0)

    command.downgrade(config, "0003_v1_2")
    inspector: Inspector = inspect(engine)
    assert not set(V13_TABLES) & set(inspector.get_table_names())
    assert not ISSUE_COLUMNS & {column["name"] for column in inspector.get_columns("issues")}
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM issues")) == 3
        assert connection.scalar(text("SELECT id FROM issues WHERE id = 'issue-critical'")) == (
            "issue-critical"
        )
    engine.dispose()


def test_v13_models_round_trip_through_orm(tmp_path: Path) -> None:
    """Persist one instance of every new ORM row and load it back."""
    database_file: Path = tmp_path / "models-v13.sqlite"
    database_url: str = f"sqlite:///{database_file}"
    config: Config = _alembic_config(database_url)
    command.upgrade(config, "head")
    engine: Engine = create_engine(database_url)
    with Session(engine) as session:
        rows: tuple[
            SavedViewRow
            | SavedViewStarRow
            | SavedViewDefaultRow
            | IssueCommentRow
            | IssueMentionRow
            | IssueSubscriptionRow
            | IssueBookmarkRow
            | ExternalIssueLinkRow
            | IssueMergeRow
            | OwnershipRuleRow
            | AlertRuleRow
            | AlertFireRow
            | NotificationSettingRow
            | IntegrationCheckRow,
            ...,
        ] = (
            SavedViewRow(
                id="view-1",
                name="Open issues",
                query="is:unresolved",
                sort="last_seen",
                visibility="personal",
                owner_member_id="member-1",
                position=0,
                created_at=AT,
                updated_at=AT,
            ),
            SavedViewStarRow(view_id="view-1", member_id="member-1"),
            SavedViewDefaultRow(member_id="member-1", view_id="view-1"),
            IssueCommentRow(
                id="comment-1",
                issue_id="issue-1",
                author_ref="member:1",
                body="Masked comment",
                created_at=AT,
            ),
            IssueMentionRow(comment_id="comment-1", member_id="member-2", created_at=AT),
            IssueSubscriptionRow(
                issue_id="issue-1",
                member_id="member-1",
                subscribed=True,
                reason="manual",
                updated_at=AT,
            ),
            IssueBookmarkRow(issue_id="issue-1", member_id="member-1", created_at=AT),
            ExternalIssueLinkRow(
                id="link-1",
                issue_id="issue-1",
                kind="github",
                url="https://example.invalid/issue/1",
                title="Issue 1",
                created_by="member:1",
                created_at=AT,
            ),
            IssueMergeRow(
                id="merge-1",
                primary_issue_id="issue-1",
                merged_issue_id="issue-2",
                merged_by="member:1",
                merged_at=AT,
            ),
            OwnershipRuleRow(
                id="ownership-1",
                position=0,
                pattern="repo/*",
                team_id="team-1",
                enabled=True,
                created_by="member:1",
                created_at=AT,
                updated_at=AT,
            ),
            AlertRuleRow(
                id="alert-rule-1",
                name="Critical alert",
                enabled=True,
                trigger_kind="new_issue",
                filters_json="{}",
                actions_json="{}",
                action_interval_minutes=60,
                created_by="member:1",
                created_at=AT,
                updated_at=AT,
            ),
            AlertFireRow(
                id="alert-fire-1",
                rule_id="alert-rule-1",
                issue_id="issue-1",
                trigger_kind="new_issue",
                outcome="sent",
                notification_count=1,
                fired_at=AT,
            ),
            NotificationSettingRow(
                member_id="member-1", category="mentions", channel="email", updated_at=AT
            ),
            IntegrationCheckRow(kind="slack", checked_at=AT, ok=True),
        )
        session.add_all(rows)
        session.commit()
        assert session.get(SavedViewRow, "view-1") == rows[0]
        assert session.get(SavedViewStarRow, ("view-1", "member-1")) == rows[1]
        assert session.get(SavedViewDefaultRow, "member-1") == rows[2]
        assert session.get(IssueCommentRow, "comment-1") == rows[3]
        assert session.get(IssueMentionRow, ("comment-1", "member-2")) == rows[4]
        assert session.get(IssueSubscriptionRow, ("issue-1", "member-1")) == rows[5]
        assert session.get(IssueBookmarkRow, ("issue-1", "member-1")) == rows[6]
        assert session.get(ExternalIssueLinkRow, "link-1") == rows[7]
        assert session.get(IssueMergeRow, "merge-1") == rows[8]
        assert session.get(OwnershipRuleRow, "ownership-1") == rows[9]
        assert session.get(AlertRuleRow, "alert-rule-1") == rows[10]
        assert session.get(AlertFireRow, "alert-fire-1") == rows[11]
        assert session.get(NotificationSettingRow, ("member-1", "mentions")) == rows[12]
        assert session.get(IntegrationCheckRow, "slack") == rows[13]
    engine.dispose()
