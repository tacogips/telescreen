"""The Diagra schema document, its DDL exports and the models must agree.

``db/schema/telescreen.diagra.jsonl`` is edited in the Diagra app and is the
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
from tests.db.diagra_schema import (
    DIAGRA_DOCUMENT,
    POSTGRESQL_DDL,
    REPOSITORY_ROOT,
    SQLITE_DDL,
    DiagraDocument,
    DiagraTableElement,
    SchemaSpec,
    diagra_schema,
    load_diagra_document,
    logical_reference_problems,
    model_schema,
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


def _diagra_cli() -> str | None:
    configured: str | None = os.environ.get("DIAGRA_CLI")
    return configured if configured else shutil.which("diagra-cli")


def test_diagra_document_matches_sqlalchemy_models() -> None:
    """Every table, column, key and index in Diagra matches the models exactly."""
    document: DiagraDocument = load_diagra_document()
    designed: SchemaSpec = diagra_schema(document)
    implemented: SchemaSpec = model_schema(_models.AuditRecordRow.metadata)
    differences: list[str] = schema_differences(designed, implemented)
    assert not differences, (
        "db/schema/telescreen.diagra.jsonl and the SQLAlchemy models disagree. Change the "
        "schema in the Diagra app first, then update the models and add an Alembic "
        "migration:\n" + "\n".join(differences)
    )


def test_logical_references_join_compatible_keys() -> None:
    """Every drawn reference joins same-typed columns to a primary or unique key."""
    document: DiagraDocument = load_diagra_document()
    assert document.relations, "the schema documents its references as relations"
    assert all(relation.semantic.logical for relation in document.relations), (
        "Telescreen enforces no foreign keys; draw references as logical relations"
    )
    problems: list[str] = logical_reference_problems(document)
    assert not problems, "\n".join(problems)


def test_logical_reference_check_reports_a_type_mismatch() -> None:
    """A reference between columns of different types is reported."""
    document: DiagraDocument = load_diagra_document()
    issues: DiagraTableElement = next(t for t in document.tables if t.id == "t-issues")
    issues.semantic.columns[0].data_type = "integer"
    problems: list[str] = logical_reference_problems(document)
    assert any("does not match issues.id (integer)" in problem for problem in problems)


def test_schema_document_has_one_erd_page() -> None:
    """All tables live on the single page the DDL exports are generated from."""
    document: DiagraDocument = load_diagra_document()
    assert document.page_ids == ["schema"]
    assert {table.page for table in document.tables} == {"schema"}


def test_schema_differences_reports_a_changed_column() -> None:
    """The comparison catches a model-only change instead of passing silently."""
    designed: SchemaSpec = diagra_schema(load_diagra_document())
    implemented: SchemaSpec = model_schema(_models.AuditRecordRow.metadata)
    renamed: SchemaSpec = SchemaSpec(
        tables=tuple(table for table in implemented.tables if table.name != "counters")
    )
    differences: list[str] = schema_differences(designed, renamed)
    assert differences == ["table counters is in Diagra but not in the models"]


def test_sqlite_export_matches_alembic_head(tmp_path: Path) -> None:
    """The committed Diagra SQLite export builds the schema Alembic migrates to."""
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


def test_postgresql_export_is_a_diagra_export() -> None:
    """The PostgreSQL DDL is the Diagra export, not hand-written SQL."""
    text: str = POSTGRESQL_DDL.read_text(encoding="utf-8")
    assert text.startswith("-- Generated by diagra for postgresql.\n")
    assert "-- Warning:" not in text
    assert text.count("CREATE TABLE ") == len(_models.AuditRecordRow.metadata.tables)


@pytest.mark.parametrize(
    ("dialect", "export"),
    [("postgresql", POSTGRESQL_DDL), ("sqlite", SQLITE_DDL)],
)
def test_exports_are_current_with_diagra_cli(dialect: str, export: Path) -> None:
    """With diagra-cli installed, the committed exports equal a fresh export."""
    cli: str | None = _diagra_cli()
    if cli is None:
        pytest.skip("requires diagra-cli (brew install --cask tacogips/tap/diagra)")
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            cli,
            "ddl",
            str(DIAGRA_DOCUMENT),
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


def test_schema_document_is_canonical_with_diagra_cli() -> None:
    """With diagra-cli installed, the document is in Diagra's canonical form."""
    cli: str | None = _diagra_cli()
    if cli is None:
        pytest.skip("requires diagra-cli (brew install --cask tacogips/tap/diagra)")
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [cli, "format", str(DIAGRA_DOCUMENT), "--check"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
