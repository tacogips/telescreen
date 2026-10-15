"""Standalone migration and startup requirements for independently owned services."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

from telescreen.cli import main
from telescreen.db.ownership import Store, tables_for


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
