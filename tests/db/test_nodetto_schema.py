"""The Nodetto schema document, its DDL exports and the models must agree.

``db/schema/telescreen.nodetto.jsonl`` is edited in the Nodetto app and is the
source of truth for Telescreen's DDL. See ``db/schema/README.md`` for the
change workflow.
"""

import asyncio
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine, Inspector

from telescreen.db import models as _models
from telescreen.db.migrate import upgrade
from tests.db.nodetto_schema import (
    NODETTO_DOCUMENT,
    POSTGRESQL_DDL,
    REPOSITORY_ROOT,
    SQLITE_DDL,
    NodettoDocument,
    NodettoFrameElement,
    NodettoTableElement,
    SchemaSpec,
    load_nodetto_document,
    logical_reference_problems,
    model_schema,
    nodetto_schema,
    schema_differences,
)


@dataclass(frozen=True)
class ReflectedTable:
    """The dialect-neutral structure SQLite reflection reports for a table."""

    name: str
    columns: tuple[tuple[str, bool, str | None], ...]
    primary_key: tuple[str, ...]
    unique_keys: frozenset[tuple[str, ...]]
    indexes: frozenset[tuple[str, tuple[str, ...]]]


def _sqlite_default(value: str | None) -> str | None:
    """Treat quoted and bare literals and booleans written as 0/1 alike."""
    if value is None:
        return None
    unquoted: str = value.strip().strip("'").lower()
    if unquoted == "false":
        return "0"
    if unquoted == "true":
        return "1"
    return unquoted


def _reflect(engine: Engine) -> tuple[ReflectedTable, ...]:
    inspector: Inspector = inspect(engine)
    tables: list[ReflectedTable] = []
    for name in sorted(inspector.get_table_names()):
        if name in {"alembic_version", "sqlite_sequence"}:
            continue
        unique_keys: set[tuple[str, ...]] = {
            tuple(str(column) for column in constraint["column_names"])
            for constraint in inspector.get_unique_constraints(name)
        }
        indexes: set[tuple[str, tuple[str, ...]]] = set()
        for index in inspector.get_indexes(name):
            columns: tuple[str, ...] = tuple(
                str(column) for column in index["column_names"] if column is not None
            )
            if index["unique"]:
                unique_keys.add(columns)
            else:
                indexes.add((str(index["name"]), columns))
        tables.append(
            ReflectedTable(
                name=name,
                columns=tuple(
                    (
                        column["name"],
                        bool(column["nullable"]),
                        _sqlite_default(column.get("default")),
                    )
                    for column in inspector.get_columns(name)
                ),
                primary_key=tuple(inspector.get_pk_constraint(name)["constrained_columns"]),
                unique_keys=frozenset(unique_keys),
                indexes=frozenset(indexes),
            )
        )
    return tuple(tables)


def _nodetto_cli() -> str | None:
    configured: str | None = os.environ.get("NODETTO_CLI")
    return configured if configured else shutil.which("nodetto-cli")


def test_nodetto_document_matches_sqlalchemy_models() -> None:
    """Every table, column, key and index in Nodetto matches the models exactly."""
    document: NodettoDocument = load_nodetto_document()
    designed: SchemaSpec = nodetto_schema(document)
    implemented: SchemaSpec = model_schema(_models.AuditRecordRow.metadata)
    differences: list[str] = schema_differences(designed, implemented)
    assert not differences, (
        "db/schema/telescreen.nodetto.jsonl and the SQLAlchemy models disagree. Change the "
        "schema in the Nodetto app first, then update the models and add an Alembic "
        "migration:\n" + "\n".join(differences)
    )


def test_logical_references_join_compatible_keys() -> None:
    """Every drawn reference joins same-typed columns to a primary or unique key."""
    document: NodettoDocument = load_nodetto_document()
    assert document.relations, "the schema documents its references as relations"
    assert all(relation.semantic.logical for relation in document.relations), (
        "Telescreen enforces no foreign keys; draw references as logical relations"
    )
    problems: list[str] = logical_reference_problems(document)
    assert not problems, "\n".join(problems)


def test_logical_reference_check_reports_a_type_mismatch() -> None:
    """A reference between columns of different types is reported."""
    document: NodettoDocument = load_nodetto_document()
    issues: NodettoTableElement = next(t for t in document.tables if t.id == "t-issues")
    issues.semantic.columns[0].data_type = "integer"
    problems: list[str] = logical_reference_problems(document)
    assert any("does not match issues.id (integer)" in problem for problem in problems)


def test_schema_document_has_one_erd_page() -> None:
    """All tables live on the single page the DDL exports are generated from."""
    document: NodettoDocument = load_nodetto_document()
    assert document.page_ids == ["schema"]
    assert {table.page for table in document.tables} == {"schema"}


SERVICE_SPLIT_FRAMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "f-projections",
        ("projection_changes", "inventory_directory_entries", "notification_directory_entries"),
    ),
    ("f-inventory-links", ("inventory_issue_links",)),
    (
        "f-notification-handoff",
        (
            "notification_outbox",
            "inventory_notification_outbox",
            "notification_inbox",
            "notification_delivery_state",
        ),
    ),
    (
        "f-service-audit",
        (
            "inventory_audit_records",
            "inventory_audit_chain_head",
            "notification_audit_records",
            "notification_audit_chain_head",
        ),
    ),
)


@pytest.mark.parametrize(("frame_id", "table_names"), SERVICE_SPLIT_FRAMES)
def test_service_split_tables_are_drawn_in_their_module_frame(
    frame_id: str, table_names: tuple[str, ...]
) -> None:
    """Tidy layout keeps a table in a frame only when it lies inside and above it."""
    document: NodettoDocument = load_nodetto_document()
    frame: NodettoFrameElement = next(f for f in document.frames if f.id == frame_id)
    left: float = frame.visual.x
    top: float = frame.visual.y
    right: float = left + frame.visual.width
    bottom: float = top + (frame.visual.height or 0)
    for name in table_names:
        table: NodettoTableElement = next(
            t for t in document.tables if t.semantic.table_name == name
        )
        assert table.visual is not None, name
        assert left <= table.visual.x and table.visual.x + table.visual.width <= right, name
        assert top <= table.visual.y <= bottom, name
        assert table.index > frame.index, f"{name} is drawn below {frame_id}"


def test_schema_differences_reports_a_changed_column() -> None:
    """The comparison catches a model-only change instead of passing silently."""
    designed: SchemaSpec = nodetto_schema(load_nodetto_document())
    implemented: SchemaSpec = model_schema(_models.AuditRecordRow.metadata)
    renamed: SchemaSpec = SchemaSpec(
        tables=tuple(table for table in implemented.tables if table.name != "counters")
    )
    differences: list[str] = schema_differences(designed, renamed)
    assert differences == ["table counters is in Nodetto but not in the models"]


def test_sqlite_export_matches_alembic_head(tmp_path: Path) -> None:
    """The committed Nodetto SQLite export builds the schema Alembic migrates to."""
    exported_file: Path = tmp_path / "exported.sqlite"
    with sqlite3.connect(exported_file) as connection:
        connection.executescript(SQLITE_DDL.read_text(encoding="utf-8"))
    migrated_file: Path = tmp_path / "migrated.sqlite"
    asyncio.run(asyncio.to_thread(upgrade, f"sqlite+aiosqlite:///{migrated_file}"))
    exported_engine: Engine = create_engine(f"sqlite:///{exported_file}")
    migrated_engine: Engine = create_engine(f"sqlite:///{migrated_file}")
    try:
        assert _reflect(exported_engine) == _reflect(migrated_engine)
    finally:
        exported_engine.dispose()
        migrated_engine.dispose()


def test_postgresql_export_is_a_nodetto_export() -> None:
    """The PostgreSQL DDL is the Nodetto export, not hand-written SQL."""
    text: str = POSTGRESQL_DDL.read_text(encoding="utf-8")
    assert text.startswith("-- Generated by nodetto for postgresql.\n")
    assert "-- Warning:" not in text
    assert text.count("CREATE TABLE ") == len(_models.AuditRecordRow.metadata.tables)


@pytest.mark.parametrize(
    ("dialect", "export"),
    [("postgresql", POSTGRESQL_DDL), ("sqlite", SQLITE_DDL)],
)
def test_exports_are_current_with_nodetto_cli(dialect: str, export: Path) -> None:
    """With nodetto-cli installed, the committed exports equal a fresh export."""
    cli: str | None = _nodetto_cli()
    if cli is None:
        pytest.skip("requires nodetto-cli (brew install --cask tacogips/tap/nodetto)")
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            cli,
            "ddl",
            str(NODETTO_DOCUMENT),
            "--dialect",
            dialect,
            "--strict",
            "--check",
            str(export),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_schema_document_is_canonical_with_nodetto_cli() -> None:
    """With nodetto-cli installed, the document is in Nodetto's canonical form."""
    cli: str | None = _nodetto_cli()
    if cli is None:
        pytest.skip("requires nodetto-cli (brew install --cask tacogips/tap/nodetto)")
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [cli, "format", str(NODETTO_DOCUMENT), "--check"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
