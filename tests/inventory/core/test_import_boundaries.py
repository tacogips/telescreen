"""Static ownership gates keep inventory SQL and master writes inside their module."""

import ast
from pathlib import Path

from sqlalchemy import Table

from telescreen.db.base import Base
from telescreen.db.models import InventoryCounterRow
from telescreen.db.ownership import INVENTORY_TABLES, MAIN_TABLES, Store, store_for

_INVENTORY_MODELS: frozenset[str] = frozenset(
    (
        "DataAssetRow",
        "DataAssetEventRow",
        "DataAssetItemRow",
        "DataFieldRow",
        "DataFieldValueRow",
        "InventoryCounterRow",
        "InventoryInboxRow",
        "InventoryInboxStreamRow",
        "InventoryAuditOutboxRow",
        "InventoryRetentionOutboxRow",
    )
)
_SRC: Path = Path(__file__).parents[3] / "src/telescreen"
_ALLOWED: tuple[str, ...] = (
    "telescreen.inventory",
    "telescreen.core",
    "telescreen.ports",
    "telescreen.masking",
)


def test_every_database_table_has_one_explicit_owner() -> None:
    """New schema tables cannot silently default to whichever database is convenient."""
    assert InventoryCounterRow.__tablename__ in INVENTORY_TABLES
    assert set(INVENTORY_TABLES).isdisjoint(MAIN_TABLES)
    assert set(INVENTORY_TABLES) | set(MAIN_TABLES) == set(Base.metadata.tables)
    assert all(store_for(name) is Store.INVENTORY for name in INVENTORY_TABLES)
    assert all(store_for(name) is Store.MAIN for name in MAIN_TABLES)
    assert (
        frozenset(
            mapper.class_.__name__
            for mapper in Base.registry.mappers
            if isinstance(mapper.local_table, Table) and mapper.local_table.name in INVENTORY_TABLES
        )
        == _INVENTORY_MODELS
    )


def test_inventory_depends_only_on_owned_models_and_public_contracts() -> None:
    """Reject supporting SQL models and concrete collector/issue/identity/API services."""
    for path in (_SRC / "inventory").rglob("*.py"):
        tree: ast.Module = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module: str = node.module or ""
                if module.startswith("telescreen.db.models"):
                    assert all(alias.name in _INVENTORY_MODELS for alias in node.names), path
                elif module.startswith("telescreen."):
                    assert module.startswith(_ALLOWED), (path, module)
                elif module == "sqlalchemy":
                    assert all(
                        alias.name not in ("text", "Table", "MetaData") for alias in node.names
                    ), path
            elif isinstance(node, ast.Import):
                assert all(not alias.name.startswith("telescreen.") for alias in node.names), path


def test_other_business_modules_cannot_import_inventory_sql_models() -> None:
    """Inventory services remain the master writer; data routers may query owned views."""
    for path in _SRC.rglob("*.py"):
        relative: Path = path.relative_to(_SRC)
        if relative.parts[0] in ("inventory", "db") or relative.as_posix() in (
            "api/routers/data_fields.py",
            "api/routers/data_asset_values.py",
        ):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "telescreen.db.models"
            ):
                assert all(alias.name not in _INVENTORY_MODELS for alias in node.names), path
