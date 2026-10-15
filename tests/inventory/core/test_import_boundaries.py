"""Static ownership gates keep inventory SQL and master writes inside their module."""

import ast
from pathlib import Path

from sqlalchemy import Table

from telescreen.db.base import Base
from telescreen.db.models import InventoryCounterRow
from telescreen.db.ownership import (
    INVENTORY_TABLES,
    MAIN_RETAINED_TABLES,
    MAIN_TABLES,
    NOTIFICATION_TABLES,
    Store,
    main_database_tables,
    store_for,
)

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
_NEW_INVENTORY_MODELS: frozenset[str] = frozenset(
    (
        "InventoryDirectoryEntryRow",
        "InventoryIssueLinkRow",
        "InventoryAuditRecordRow",
        "InventoryAuditChainHeadRow",
        "InventoryNotificationOutboxRow",
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
    assert set(INVENTORY_TABLES).isdisjoint(NOTIFICATION_TABLES)
    assert set(MAIN_TABLES).isdisjoint(NOTIFICATION_TABLES)
    assert set(INVENTORY_TABLES) | set(MAIN_TABLES) | set(NOTIFICATION_TABLES) == set(
        Base.metadata.tables
    )
    assert (len(MAIN_TABLES), len(INVENTORY_TABLES), len(NOTIFICATION_TABLES)) == (50, 15, 9)
    assert len(Base.metadata.tables) == 74
    assert len(main_database_tables()) == 54
    assert set(MAIN_RETAINED_TABLES) == {
        "notifications",
        "notification_templates",
        "notification_settings",
        "retention_notification_receipts",
    }
    assert all(store_for(name) is Store.INVENTORY for name in INVENTORY_TABLES)
    assert all(store_for(name) is Store.MAIN for name in MAIN_TABLES)
    assert all(store_for(name) is Store.NOTIFICATION for name in NOTIFICATION_TABLES)
    assert (
        frozenset(
            mapper.class_.__name__
            for mapper in Base.registry.mappers
            if isinstance(mapper.local_table, Table) and mapper.local_table.name in INVENTORY_TABLES
        )
        == _INVENTORY_MODELS | _NEW_INVENTORY_MODELS
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
    """Only the named Inventory, replica and audit adapters may import new Inventory rows."""
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
                imported: set[str] = {alias.name for alias in node.names}
                assert not imported.intersection(_INVENTORY_MODELS), path
                new_imports: set[str] = imported.intersection(_NEW_INVENTORY_MODELS)
                if new_imports:
                    assert relative.parts[0] in ("inventory_service", "replica") or (
                        relative.as_posix() == "audit/chain.py"
                    ), path
