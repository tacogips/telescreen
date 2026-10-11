"""Regression tests for the idempotent v1.2 schema migration."""

import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, Inspector
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import Session

from telescreen.db.migrate import upgrade
from telescreen.db.models import (
    MemberRow,
    NotificationRow,
    NotificationTemplateRow,
    TeamMembershipRow,
)

_backfill_module: ModuleType = import_module("telescreen.db.migrations.versions.0003_v1_2")
_backfill_member_roles: Callable[[Connection], None] = _backfill_module._backfill_member_roles

V12_COLUMNS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "notifications",
        frozenset(
            {
                "event_kind",
                "template_name",
                "template_version",
                "set_name",
                "target_index",
                "error_code",
            }
        ),
    ),
    ("members", frozenset({"role", "last_sign_in_at"})),
    ("team_memberships", frozenset({"origin"})),
)


def _alembic_config(database_url: str) -> Config:
    """Build an Alembic config matching the programmatic migration entry point."""
    migration_directory: Path = Path(__file__).parents[2] / "src/telescreen/db/migrations"
    config: Config = Config()
    config.set_main_option("script_location", migration_directory.as_posix())
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _assert_v12_schema(engine: Engine) -> None:
    """Assert the v1.2 table, columns and composite indexes are present."""
    inspector: Inspector = inspect(engine)
    table_names: set[str] = set(inspector.get_table_names())
    assert "notification_templates" in table_names
    for table_name, expected_columns in V12_COLUMNS:
        actual_columns: set[str] = {column["name"] for column in inspector.get_columns(table_name)}
        assert expected_columns <= actual_columns
    template_indexes: set[str] = {
        str(index["name"])
        for index in inspector.get_indexes("notification_templates")
        if index["name"] is not None
    }
    member_indexes: set[str] = {
        str(index["name"])
        for index in inspector.get_indexes("members")
        if index["name"] is not None
    }
    assert "ix_notification_templates_name_active" in template_indexes
    assert "ix_members_role_active" in member_indexes


def _seed_legacy_members(engine: Engine) -> None:
    """Insert members and token scopes into a v1.1 schema for role backfill."""
    observed_at: str = datetime(2026, 10, 11, tzinfo=UTC).isoformat()
    with engine.begin() as connection:
        for member_id in ("member-a", "member-b", "member-c", "member-d", "member-e"):
            connection.execute(
                text(
                    "INSERT INTO members (id, handle, display_name, active, created_at, "
                    "updated_at) "
                    "VALUES (:id, :handle, :display_name, 1, :observed_at, :observed_at)"
                ),
                {
                    "id": member_id,
                    "handle": member_id,
                    "display_name": member_id,
                    "observed_at": observed_at,
                },
            )
        token_rows: tuple[tuple[str, str, str | None], ...] = (
            ("token-a", "admin,read,write", "member-a"),
            ("token-b1", "read,write", "member-b"),
            ("token-b2", "read", "member-b"),
            ("token-c", "read", "member-c"),
            ("token-d", "ingest", "member-d"),
            ("token-e", "admin", None),
        )
        for token_id, scopes, member_id in token_rows:
            connection.execute(
                text(
                    "INSERT INTO api_tokens "
                    "(id, label, token_hash, scopes, member_id, created_at, revoked_at) "
                    "VALUES (:id, :label, :token_hash, :scopes, :member_id, "
                    ":created_at, :revoked_at)"
                ),
                {
                    "id": token_id,
                    "label": token_id,
                    "token_hash": f"hash-{token_id}",
                    "scopes": scopes,
                    "member_id": member_id,
                    "created_at": observed_at,
                    "revoked_at": observed_at if token_id == "token-c" else None,
                },
            )
        connection.execute(
            text(
                "INSERT INTO teams (id, slug, name, created_at, updated_at) VALUES "
                "('team-1', 'team-1', 'Team 1', :at, :at)"
            ),
            {"at": observed_at},
        )
        connection.execute(
            text("INSERT INTO team_memberships (team_id, member_id) VALUES ('team-1', 'member-a')")
        )


def test_fresh_schema_has_v12_additions_and_upgrade_is_idempotent(tmp_path: Path) -> None:
    """Upgrade a fresh database and safely apply revision 0003 a second time."""
    database_file: Path = tmp_path / "fresh-v12.sqlite"
    database_url: str = f"sqlite+aiosqlite:///{database_file}"
    asyncio.run(asyncio.to_thread(upgrade, database_url))
    config: Config = _alembic_config(database_url)
    command.upgrade(config, "head")
    engine: Engine = create_engine(f"sqlite:///{database_file}")
    _assert_v12_schema(engine)
    engine.dispose()


def test_v11_schema_backfills_roles_and_round_trips_v12_models(tmp_path: Path) -> None:
    """Backfill only newly added roles and round-trip all new ORM fields."""
    database_file: Path = tmp_path / "existing-v11.sqlite"
    database_url: str = f"sqlite+aiosqlite:///{database_file}"
    config: Config = _alembic_config(database_url)
    asyncio.run(asyncio.to_thread(upgrade, database_url))
    command.downgrade(config, "0002_v1_1")
    engine: Engine = create_engine(f"sqlite:///{database_file}")
    _seed_legacy_members(engine)
    command.upgrade(config, "head")
    _assert_v12_schema(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT role FROM members WHERE id = 'member-a'")) == "admin"
        assert (
            connection.scalar(text("SELECT role FROM members WHERE id = 'member-b'")) == "responder"
        )
        assert connection.scalar(text("SELECT role FROM members WHERE id = 'member-c'")) is None
        assert connection.scalar(text("SELECT role FROM members WHERE id = 'member-d'")) is None
        assert connection.scalar(text("SELECT role FROM members WHERE id = 'member-e'")) is None
        assert (
            connection.scalar(
                text("SELECT origin FROM team_memberships WHERE member_id = 'member-a'")
            )
            == "manual"
        )
    with engine.begin() as connection:
        connection.execute(text("UPDATE members SET role = 'viewer' WHERE id = 'member-a'"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT role FROM members WHERE id = 'member-a'")) == "viewer"

    observed_at: datetime = datetime(2026, 10, 11, tzinfo=UTC)
    with Session(engine) as session:
        member: MemberRow = MemberRow(
            id="member-model",
            handle="model",
            display_name="Model",
            created_at=observed_at,
            updated_at=observed_at,
        )
        assert member.role is None
        assert member.last_sign_in_at is None
        notification: NotificationRow = NotificationRow(
            id="notification-model",
            channel="email",
            recipient_ref="member:model",
            subject="Subject",
            body="Body",
            dlp_verdict="clean",
            status="sent",
            created_at=observed_at,
            event_kind="workflow_notify",
            template_name="workflow.default",
            template_version=1,
            set_name="responders",
            target_index=0,
            error_code=None,
        )
        template: NotificationTemplateRow = NotificationTemplateRow(
            id="template-model",
            name="workflow.default",
            version=1,
            event_kind="workflow_notify",
            subject="Subject",
            body="Body",
            active=True,
            created_by="member:model",
            created_at=observed_at,
        )
        membership: TeamMembershipRow = TeamMembershipRow(
            team_id="team-model",
            member_id="member-model",
        )
        assert membership.origin == "manual"
        session.add_all([member, notification, template, membership])
        session.commit()
        assert session.get(NotificationRow, "notification-model") == notification
        assert session.get(NotificationTemplateRow, "template-model") == template
        assert session.get(TeamMembershipRow, ("team-model", "member-model")) == membership
        session.add(
            NotificationTemplateRow(
                id="template-duplicate",
                name="workflow.default",
                version=1,
                event_kind="workflow_notify",
                subject="Duplicate",
                body="Duplicate",
                active=False,
                created_by="member:model",
                created_at=observed_at,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    command.downgrade(config, "0002_v1_1")
    command.upgrade(config, "head")
    _assert_v12_schema(engine)
    engine.dispose()


def _verify_postgres_backfill(connection: Connection) -> None:
    """Exercise the same backfill helper on isolated PostgreSQL temporary tables."""
    connection.execute(text("CREATE TEMPORARY TABLE members (id VARCHAR(36), role VARCHAR(16))"))
    connection.execute(
        text(
            "CREATE TEMPORARY TABLE api_tokens "
            "(member_id VARCHAR(36), scopes VARCHAR(512), revoked_at TIMESTAMP WITH TIME ZONE)"
        )
    )
    connection.execute(text("INSERT INTO members (id) VALUES ('member-pg-admin')"))
    connection.execute(text("INSERT INTO members (id) VALUES ('member-pg-viewer')"))
    connection.execute(
        text(
            "INSERT INTO api_tokens (member_id, scopes, revoked_at) "
            "VALUES ('member-pg-admin', 'read,admin', NULL), "
            "('member-pg-viewer', 'read', NULL), ('member-pg-viewer', 'admin', now())"
        )
    )
    _backfill_member_roles(connection)
    assert (
        connection.scalar(text("SELECT role FROM members WHERE id = 'member-pg-admin'")) == "admin"
    )
    assert (
        connection.scalar(text("SELECT role FROM members WHERE id = 'member-pg-viewer'"))
        == "viewer"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_role_backfill_uses_active_bound_token_scopes() -> None:
    """Verify D12 role precedence on PostgreSQL when the integration URL is set."""
    database_url: str | None = os.environ.get("TELESCREEN_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("requires TELESCREEN_TEST_DATABASE_URL")
    engine: AsyncEngine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(_verify_postgres_backfill)
    await engine.dispose()
