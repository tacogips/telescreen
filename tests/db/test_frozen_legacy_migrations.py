"""Historical targets reproduce their own schema instead of the latest ORM schema."""

import ast
from pathlib import Path

import pytest
from sqlalchemy import Column, Engine, Integer, inspect
from sqlalchemy import create_engine as sync_engine

from telescreen.db.base import Base
from telescreen.db.migrate import upgrade
from telescreen.db.models import InventoryCounterRow


@pytest.mark.parametrize(
    ("revision", "table_count", "present", "absent"),
    (
        ("0001_initial", 27, "members", "member_identities"),
        ("0002_v1_1", 33, "member_identities", "notification_templates"),
        ("0003_v1_2", 34, "notification_templates", "alert_rules"),
        ("0004_v1_3", 48, "alert_rules", "data_assets"),
        ("0005_v1_4", 53, "data_assets", "inventory_inbox"),
        ("0006_inventory_handoff", 57, "inventory_inbox", "inventory_audit_outbox"),
        ("0007_inventory_audit", 59, "inventory_audit_outbox", "inventory_retention_outbox"),
        ("0008_retention_handoff", 61, "inventory_retention_outbox", "inventory_counters"),
        ("0009_inventory_counter", 62, "inventory_counters", "notification_audit_records"),
    ),
)
def test_historical_targets_have_only_their_released_additions(
    tmp_path: Path, revision: str, table_count: int, present: str, absent: str
) -> None:
    """A fresh old target is a genuine incremental-upgrade fixture without manual DDL."""
    path: Path = tmp_path / "history.db"
    url: str = f"sqlite+aiosqlite:///{path}"
    upgrade(url, revision)
    engine: Engine = sync_engine(f"sqlite:///{path}")
    try:
        names: set[str] = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert len(names) == table_count
        assert present in names
        assert absent not in names
        if "data_field_values" in names:
            assert (
                "revision_id"
                in {column["name"] for column in inspect(engine).get_columns("data_field_values")}
            ) == (revision in ("0008_retention_handoff", "0009_inventory_counter"))
        upgrade(url)
        assert len(set(inspect(engine).get_table_names()) - {"alembic_version"}) == 74
    finally:
        engine.dispose()


def test_legacy_upgrades_ignore_future_model_changes(tmp_path: Path) -> None:
    """A future main-model edit cannot change the old graph or its final table shape."""
    assert InventoryCounterRow.__tablename__ == "inventory_counters"
    column: Column[int] = Column("future_probe", Integer, nullable=True)
    Base.metadata.tables["members"].append_column(column)
    path: Path = tmp_path / "frozen.db"
    try:
        upgrade(f"sqlite+aiosqlite:///{path}")
        engine: Engine = sync_engine(f"sqlite:///{path}")
        try:
            assert "future_probe" not in {
                item["name"] for item in inspect(engine).get_columns("members")
            }
        finally:
            engine.dispose()
    finally:
        Base.metadata.tables["members"]._columns.remove(column)


def test_legacy_migrations_do_not_import_live_models() -> None:
    """Revisions and environment use only frozen definitions."""
    root: Path = Path(__file__).parents[2] / "src/telescreen/db/migrations"
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in ("telescreen.db.models", "telescreen.db.base")
                assert not (
                    node.module == "telescreen.db"
                    and any(alias.name == "models" for alias in node.names)
                )
