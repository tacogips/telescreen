"""Database settings, CLI migrations and ownership of runtime connection pools."""

from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy import create_engine as sync_engine
from sqlalchemy.ext.asyncio import AsyncEngine

from telescreen.cli import main
from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.db.ownership import Store, tables_for
from telescreen.wiring import Container, build_container, close_container
from tests.e2e.harness import PairEnv
from tests.support.db import SqliteHarness, make_sqlite_harness


def test_configured_cli_migrates_both_stores_without_runtime_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A split deployment initializes both complete owner schemas through the real CLI."""
    main_path: Path = tmp_path / "main.db"
    inventory_path: Path = tmp_path / "inventory.db"
    monkeypatch.setenv("TELESCREEN_DATABASE_URL", f"sqlite+aiosqlite:///{main_path}")
    monkeypatch.setenv("TELESCREEN_INVENTORY_DATABASE_URL", f"sqlite+aiosqlite:///{inventory_path}")
    assert main(["migrate"]) == 0
    assert main(["migrate"]) == 0
    # Single process mode keeps the notification store co-located in main (design
    # section 12, owner clarification O1).
    expected: tuple[tuple[Path, tuple[Store, ...]], ...] = (
        (main_path, (Store.MAIN, Store.NOTIFICATION)),
        (inventory_path, (Store.INVENTORY,)),
    )
    for path, stores in expected:
        engine: Engine = sync_engine(f"sqlite:///{path}")
        try:
            with engine.connect() as connection:
                assert set(inspect(connection).get_table_names()) == {
                    table
                    for store in stores
                    for table in (*tables_for(store), f"alembic_version_{store}")
                }
        finally:
            engine.dispose()


def test_cli_can_migrate_only_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Inventory migration credentials never need to connect to the main database."""
    main_path: Path = tmp_path / "main.db"
    inventory_path: Path = tmp_path / "inventory.db"
    monkeypatch.setenv("TELESCREEN_DATABASE_URL", f"sqlite+aiosqlite:///{main_path}")
    monkeypatch.setenv("TELESCREEN_INVENTORY_DATABASE_URL", f"sqlite+aiosqlite:///{inventory_path}")
    assert main(["migrate", "--store", "inventory"]) == 0
    assert inventory_path.exists()
    assert not main_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["default", "same", "separate", "injected"])
async def test_runtime_shares_defaults_and_closes_only_owned_inventory_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    """Distinct URLs get an owned pool; explicit injection overrides its configured URL."""
    main_url: str = f"sqlite+aiosqlite:///{tmp_path / 'main.db'}"
    inventory_url: str = f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}"
    env: PairEnv = PairEnv(
        (
            ("TELESCREEN_DATABASE_URL", main_url),
            (
                "TELESCREEN_INVENTORY_DATABASE_URL",
                ""
                if selection == "default"
                else main_url
                if selection == "same"
                else inventory_url,
            ),
            ("TELESCREEN_FINGERPRINT_KEY", "env:TEST_KEY"),
            ("TEST_KEY", "t" * 32),
        )
    )
    settings: TelescreenSettings = load_settings(env)
    assert settings.inventory_database_url == (
        None if selection == "default" else main_url if selection == "same" else inventory_url
    )
    injected: SqliteHarness = await make_sqlite_harness(store=Store.INVENTORY)
    closed: list[AsyncEngine] = []

    async def dispose(engine: AsyncEngine, close: bool = True) -> None:
        closed.append(engine)
        await original_dispose(engine, close)

    original_dispose: Callable[[AsyncEngine, bool], Awaitable[None]] = AsyncEngine.dispose
    async with httpx.AsyncClient() as http:
        container: Container = build_container(
            settings,
            env,
            http,
            inventory_sessions=injected.sessions if selection == "injected" else None,
        )
        try:
            if selection in ("default", "same"):
                assert container.inventory_sessions is container.sessions
                assert container.inventory_engine is None
            elif selection == "injected":
                assert container.inventory_sessions is injected.sessions
                assert container.inventory_engine is None
            else:
                assert container.inventory_sessions is not container.sessions
                assert container.inventory_engine is not None
                assert str(container.inventory_engine.url) == inventory_url
            async with container.sessions() as session:
                assert await session.scalar(text("SELECT 1")) == 1
            async with container.inventory_sessions() as session:
                assert await session.scalar(text("SELECT 1")) == 1
            monkeypatch.setattr(AsyncEngine, "dispose", dispose)
        finally:
            await close_container(container)
        assert closed == (
            [container.inventory_engine, container.engine]
            if container.inventory_engine is not None
            else [container.engine]
        )
        assert not http.is_closed
    await injected.close()
