"""Offline worker and collector command behavior using file-backed SQLite."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine as sync_engine
from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.engine.result import ScalarResult
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from telescreen.cli import main
from telescreen.collectors.codec import selector_to_json
from telescreen.core.json import dump_json
from telescreen.core.vocabulary import CollectorKind, TriggerMode
from telescreen.db.base import Base
from telescreen.db.models import (
    AuditChainHeadRow,
    CollectorConfigRow,
    CounterRow,
    MapJobRow,
)
from telescreen.db.ownership import NOTIFICATION_TABLES, main_database_tables
from telescreen.db.session import create_sessionmaker
from telescreen.ports.collectors import GitHubSelector


def _configure_environment(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    """Set only the required CLI database and fingerprint configuration."""
    monkeypatch.setenv("TELESCREEN_DATABASE_URL", database_url)
    monkeypatch.setenv("TELESCREEN_FINGERPRINT_KEY", "env:TELESCREEN_TEST_KEY")
    monkeypatch.setenv("TELESCREEN_TEST_KEY", "t" * 32)
    monkeypatch.delenv("TELESCREEN_MANAGED_LLM_PROVIDER", raising=False)


async def _seed_database(database_url: str) -> AsyncEngine:
    """Create schema and required genesis rows plus one scheduled GitHub config."""
    engine: AsyncEngine = create_async_engine(database_url)
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    selector: GitHubSelector = GitHubSelector("owner", ("repo",), (), ())
    async with sessions.begin() as session:
        session.add(CounterRow(name="issue_number", value=0))
        session.add(AuditChainHeadRow(id=1, seq=0, hash="0" * 64))
        session.add(
            CollectorConfigRow(
                id="collector-id",
                name="repo-sweep",
                kind=CollectorKind.GITHUB.value,
                trigger_mode=TriggerMode.SCHEDULE.value,
                cron=None,
                sweep_cron=None,
                sweep_max_concurrency=4,
                selector_json=dump_json(selector_to_json(selector)),
                credential_ref=None,
                cursor_json=None,
                enabled=True,
                last_scheduled_at=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    return engine


@pytest.fixture
def cli_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, AsyncEngine]:
    """Prepare a disposable SQLite database and configure the CLI environment."""
    database_path: Path = tmp_path / "cli.db"
    database_url: str = f"sqlite+aiosqlite:///{database_path}"
    engine: AsyncEngine = asyncio.run(_seed_database(database_url))
    _configure_environment(monkeypatch, database_url)
    return database_path, engine


def test_worker_and_collector_sweep_cli(
    cli_database: tuple[Path, AsyncEngine], capsys: pytest.CaptureFixture[str]
) -> None:
    """Run worker once and assert collector sweep CLI status and durable job."""
    _database_path: Path
    engine: AsyncEngine
    _database_path, engine = cli_database
    try:
        assert main(["worker", "--once"]) == 0
        assert main(["collector", "sweep", "nope"]) == 3
        assert main(["collector", "sweep", "repo-sweep"]) == 0
        assert "sweep=" in capsys.readouterr().out
        map_jobs: tuple[MapJobRow, ...] = asyncio.run(_map_jobs(engine))
        assert len(map_jobs) == 1
        assert main(["collector", "sweep", "repo-sweep"]) == 1
        assert "collector sweep already active" in capsys.readouterr().err
    finally:
        asyncio.run(engine.dispose())


async def _map_jobs(engine: AsyncEngine) -> tuple[MapJobRow, ...]:
    """Read persisted map jobs after the CLI command completes."""
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)
    async with sessions() as session:
        result: ScalarResult[MapJobRow] = await session.scalars(select(MapJobRow))
        return tuple(result.all())


def test_token_create_cli_prints_one_token_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Create a scoped API token and keep its output to one line."""
    database_path: Path = tmp_path / "token.db"
    database_url: str = f"sqlite+aiosqlite:///{database_path}"
    engine: AsyncEngine = asyncio.run(_seed_database(database_url))
    _configure_environment(monkeypatch, database_url)
    try:
        assert main(["token", "create", "--label", "x", "--scopes", "read"]) == 0
        output: str = capsys.readouterr().out
        assert len(output.splitlines()) == 1
    finally:
        asyncio.run(engine.dispose())


def test_notification_migration_requires_url_and_creates_only_notification_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Select the notification owner URL and create only its isolated owner schema."""
    main_path: Path = tmp_path / "main.db"
    notification_path: Path = tmp_path / "notification.db"
    database_url: str = f"sqlite+aiosqlite:///{main_path}"
    _configure_environment(monkeypatch, database_url)
    assert main(["migrate", "--store", "notification"]) == 3
    monkeypatch.setenv(
        "TELESCREEN_NOTIFICATION_DATABASE_URL", f"sqlite+aiosqlite:///{notification_path}"
    )
    assert main(["migrate", "--store", "notification"]) == 0
    engine: Engine = sync_engine(f"sqlite:///{notification_path}")
    try:
        assert set(inspect(engine).get_table_names()) == {
            *NOTIFICATION_TABLES,
            "alembic_version_notification",
        }
    finally:
        engine.dispose()


def test_single_process_projection_resync_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Require full split topology for projection recovery commands."""
    database_url: str = f"sqlite+aiosqlite:///{tmp_path / 'projection.db'}"
    _configure_environment(monkeypatch, database_url)
    assert main(["projections", "resync"]) == 2
    assert "projections require split mode" in capsys.readouterr().err


def test_split_notify_test_requires_notification_service_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep synchronous test sends on the Notification service in split mode."""
    database_url: str = f"sqlite+aiosqlite:///{tmp_path / 'split-notify.db'}"
    _configure_environment(monkeypatch, database_url)
    monkeypatch.setenv("TELESCREEN_INVENTORY_SERVICE_URL", "http://inventory")
    monkeypatch.setenv("TELESCREEN_INVENTORY_SERVICE_TOKEN", "env:INVENTORY_TOKEN")
    monkeypatch.setenv("INVENTORY_TOKEN", "i" * 32)
    monkeypatch.setenv("TELESCREEN_NOTIFICATION_SERVICE_URL", "http://notification")
    monkeypatch.setenv("TELESCREEN_NOTIFICATION_SERVICE_TOKEN", "env:NOTIFICATION_TOKEN")
    monkeypatch.setenv("NOTIFICATION_TOKEN", "n" * 32)
    assert main(["notify", "test", "--channel", "email", "--to", "member:alice"]) == 2
    assert "use telescreen-notification send-test" in capsys.readouterr().err


def test_split_default_migration_creates_main_tables_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default migration in split mode upgrades only the main owner history."""
    database_path: Path = tmp_path / "split-main.db"
    database_url: str = f"sqlite+aiosqlite:///{database_path}"
    _configure_environment(monkeypatch, database_url)
    monkeypatch.setenv("TELESCREEN_INVENTORY_SERVICE_URL", "http://inventory")
    monkeypatch.setenv("TELESCREEN_INVENTORY_SERVICE_TOKEN", "env:INVENTORY_TOKEN")
    monkeypatch.setenv("INVENTORY_TOKEN", "i" * 32)
    monkeypatch.setenv("TELESCREEN_NOTIFICATION_SERVICE_URL", "http://notification")
    monkeypatch.setenv("TELESCREEN_NOTIFICATION_SERVICE_TOKEN", "env:NOTIFICATION_TOKEN")
    monkeypatch.setenv("NOTIFICATION_TOKEN", "n" * 32)
    assert main(["migrate"]) == 0

    engine: Engine = sync_engine(f"sqlite:///{database_path}")
    try:
        assert set(inspect(engine).get_table_names()) == {
            *main_database_tables(),
            "alembic_version_main",
        }
    finally:
        engine.dispose()
