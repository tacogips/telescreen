"""Immutable schema definitions for legacy revisions 0001 through 0005.

The snapshot was captured from the Nodetto-verified v1.4 models, excluding later
additions. Introduction versions come from the released incremental revisions.
Never regenerate this resource from new ORM models; add explicit migrations instead.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)


@dataclass(frozen=True)
class FrozenColumn:
    """A column definition at its first legacy revision."""

    name: str
    kind: str
    length: int | None
    nullable: bool
    primary_key: bool
    default: str | None
    introduced: int
    autoincrement: bool | Literal["auto", "ignore_fk"]


@dataclass(frozen=True)
class FrozenUnique:
    """A named or anonymous uniqueness constraint."""

    name: str | None
    columns: tuple[str, ...]


@dataclass(frozen=True)
class FrozenIndex:
    """A frozen index definition."""

    name: str
    columns: tuple[str, ...]
    unique: bool


@dataclass(frozen=True)
class FrozenTable:
    """One table with its history and physical constraints."""

    name: str
    introduced: int
    columns: tuple[FrozenColumn, ...]
    uniques: tuple[FrozenUnique, ...]
    indexes: tuple[FrozenIndex, ...]


@dataclass(frozen=True)
class FrozenSchema:
    """Packaged legacy history independent of runtime model imports."""

    tables: tuple[FrozenTable, ...]


def _column_type(column: FrozenColumn) -> String | Text | Integer | Boolean | DateTime | Float:
    """Preserve physical types and timezone handling on both supported dialects."""
    match column.kind:
        case "String":
            return String(column.length)
        case "Text":
            return Text()
        case "Integer":
            return Integer()
        case "Boolean":
            return Boolean()
        case "DateTime":
            return DateTime(timezone=True)
        case "Float":
            return Float()
        case _:
            raise RuntimeError("unsupported frozen legacy column type")


def metadata_for(version: int) -> MetaData:
    """Build only the tables and columns that existed at a historical target."""
    if version not in range(1, 6):
        raise ValueError("unsupported frozen legacy revision")
    return read_metadata(Path(__file__).with_name("legacy_schema.json"), version)


def read_metadata(path: Path, version: int) -> MetaData:
    """Build tables from an immutable packaged schema resource."""
    snapshot: FrozenSchema = TypeAdapter(FrozenSchema).validate_json(path.read_bytes())
    metadata: MetaData = MetaData()
    for definition in snapshot.tables:
        if definition.introduced > version:
            continue
        table: Table = Table(definition.name, metadata)
        for column in definition.columns:
            if column.introduced <= version:
                table.append_column(
                    Column(
                        column.name,
                        _column_type(column),
                        nullable=column.nullable,
                        primary_key=column.primary_key,
                        autoincrement=column.autoincrement,
                        server_default=column.default,
                    )
                )
        present: set[str] = set(table.c.keys())
        for unique in definition.uniques:
            if set(unique.columns) <= present:
                table.append_constraint(UniqueConstraint(*unique.columns, name=unique.name))
        for index in definition.indexes:
            if set(index.columns) <= present:
                Index(index.name, *(table.c[name] for name in index.columns), unique=index.unique)
    return metadata
