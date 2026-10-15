"""Standalone migration and startup requirements for independently owned services."""

from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

from telescreen.cli import main
from telescreen.core.config import TelescreenSettings, load_settings
from telescreen.core.errors import ConfigError
from telescreen.db.ownership import Store, tables_for
from telescreen.inventory_service.app import build_inventory_runtime
from telescreen.wiring import build_container
from tests.e2e.harness import PairEnv


def test_inventory_migration_requires_no_main_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real CLI can migrate inventory with no main settings or runtime secrets."""
    path: Path = tmp_path / "inventory.db"
    monkeypatch.delenv("TELESCREEN_DATABASE_URL", raising=False)
    monkeypatch.setenv("TELESCREEN_INVENTORY_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    assert main(["migrate", "--store", "inventory"]) == 0
    engine: Engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) == {
                *tables_for(Store.INVENTORY),
                "alembic_version_inventory",
            }
    finally:
        engine.dispose()


def test_remote_default_migration_creates_main_tables_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote bureau's normal migrate never recreates an inventory owner locally."""
    path: Path = tmp_path / "main.db"
    monkeypatch.setenv("TELESCREEN_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    monkeypatch.setenv("TELESCREEN_INVENTORY_SERVICE_URL", "http://inventory")
    monkeypatch.delenv("TELESCREEN_INVENTORY_DATABASE_URL", raising=False)
    assert main(["migrate"]) == 0
    engine: Engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) == {
                *tables_for(Store.MAIN),
                "alembic_version_main",
            }
    finally:
        engine.dispose()


@pytest.mark.parametrize("token", [None, "env:EMPTY"])
async def test_missing_service_credentials_fail_before_resources_are_created(
    token: str | None,
) -> None:
    """Neither service silently starts an unauthenticated internal API."""
    env: PairEnv = PairEnv((("EMPTY", ""), ("FP", "f" * 32)))
    settings: TelescreenSettings = replace(
        load_settings(env),
        database_url="sqlite+aiosqlite:///:memory:",
        inventory_database_url="sqlite+aiosqlite:///:memory:",
        inventory_service_url="http://inventory",
        bureau_service_url="http://bureau",
        fingerprint_key_ref="env:FP",
        inventory_service_token_ref=token,
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ConfigError):
            build_container(settings, env, http)
        with pytest.raises(ConfigError):
            build_inventory_runtime(settings, env, http)


@pytest.mark.parametrize(
    "module,forbidden",
    [
        (
            "telescreen.inventory_service.cli",
            (
                "telescreen.wiring",
                "telescreen.wiring_v14",
                "telescreen.collectors.service",
                "telescreen.notifications.retention",
                "telescreen.issues.service",
            ),
        ),
        ("telescreen.wiring", ("telescreen.inventory", "telescreen.wiring_v14")),
    ],
)
def test_process_entry_points_do_not_import_the_other_owner(
    module: str, forbidden: tuple[str, ...]
) -> None:
    """Future inventory implementation changes do not become bureau import dependencies."""
    import subprocess
    import sys

    script: str = (
        "import importlib, sys; "
        f"importlib.import_module({module!r}); "
        "assert not any(name == prefix or name.startswith(prefix + '.') "
        f"for name in sys.modules for prefix in {forbidden!r})"
    )
    result: subprocess.CompletedProcess[bytes] = subprocess.run(
        (sys.executable, "-c", script), capture_output=True, timeout=30
    )
    assert result.returncode == 0, result.stderr.decode()
