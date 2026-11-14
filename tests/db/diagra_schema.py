"""Compare the Diagra ERD schema document with the SQLAlchemy models.

``db/schema/telescreen.diagra.jsonl`` is the source of truth for Telescreen's
DDL and is edited in the Diagra app. These helpers parse that document and the
SQLAlchemy metadata into one normalized shape so tests can report every
difference between the designed schema and the implemented models.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pydantic
from pydantic import Field, TypeAdapter
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.types import TypeEngine

REPOSITORY_ROOT: Path = Path(__file__).resolve().parents[2]
SCHEMA_DIRECTORY: Path = REPOSITORY_ROOT / "db" / "schema"
DIAGRA_DOCUMENT: Path = SCHEMA_DIRECTORY / "telescreen.diagra.jsonl"
POSTGRESQL_DDL: Path = SCHEMA_DIRECTORY / "telescreen.postgresql.sql"
SQLITE_DDL: Path = SCHEMA_DIRECTORY / "telescreen.sqlite.sql"
SCHEMA_PAGE_ID: str = "schema"

BOOLEAN_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("0", "false"),
    ("1", "true"),
    ("false", "false"),
    ("true", "true"),
)


@pydantic.dataclasses.dataclass
class DiagraRecordHeader:
    """The fields every Diagra JSONL record shares."""

    kind: str
    id: str | None = None
    page: str | None = None
    type: str | None = None


@pydantic.dataclasses.dataclass
class DiagraColumn:
    """One ERD column; an omitted ``nullable`` means NOT NULL in Diagra."""

    id: str
    name: str
    data_type: str = Field(alias="dataType")
    pk: bool = False
    nullable: bool = False
    auto_increment: bool = Field(default=False, alias="autoIncrement")
    default_expression: str | None = Field(default=None, alias="defaultExpression")
    generated_expression: str | None = Field(default=None, alias="generatedExpression")


@pydantic.dataclasses.dataclass
class DiagraIndex:
    """An ordered (unique) index over stable column ids."""

    id: str
    columns: list[str]
    name: str | None = None
    unique: bool = False


@pydantic.dataclasses.dataclass
class DiagraCheck:
    """A table check constraint predicate."""

    id: str
    expression: str
    name: str | None = None


@pydantic.dataclasses.dataclass
class DiagraTable:
    """The semantic payload of an ``erd.table`` element."""

    table_name: str = Field(alias="tableName")
    columns: list[DiagraColumn] = Field(default_factory=list)
    indexes: list[DiagraIndex] = Field(default_factory=list)
    checks: list[DiagraCheck] = Field(default_factory=list)


@pydantic.dataclasses.dataclass
class DiagraTableElement:
    """An ``erd.table`` element record."""

    id: str
    page: str
    semantic: DiagraTable


@pydantic.dataclasses.dataclass
class DiagraEndpoint:
    """A relation endpoint anchored to one or more table columns."""

    table: str
    column: str | None = None
    columns: list[str] = Field(default_factory=list)

    def column_ids(self) -> list[str]:
        """Return the ordered endpoint columns in either spelling."""
        if self.columns:
            return list(self.columns)
        return [self.column] if self.column is not None else []


@pydantic.dataclasses.dataclass
class DiagraRelation:
    """The semantic payload of an ``erd.relation`` element.

    An enforced relation is a foreign key. A ``logical`` relation documents a
    join path the database does not enforce; Diagra draws it dashed and its
    DDL export emits no constraint for it.
    """

    to: DiagraEndpoint
    cardinality: str
    from_: DiagraEndpoint = Field(alias="from")
    on_delete: str | None = Field(default=None, alias="onDelete")
    on_update: str | None = Field(default=None, alias="onUpdate")
    logical: bool = False
    label: str | None = None


@pydantic.dataclasses.dataclass
class DiagraRelationElement:
    """An ``erd.relation`` element record."""

    id: str
    page: str
    semantic: DiagraRelation


HEADER_ADAPTER: TypeAdapter[DiagraRecordHeader] = TypeAdapter(DiagraRecordHeader)
TABLE_ADAPTER: TypeAdapter[DiagraTableElement] = TypeAdapter(DiagraTableElement)
RELATION_ADAPTER: TypeAdapter[DiagraRelationElement] = TypeAdapter(DiagraRelationElement)


@dataclass(frozen=True)
class ColumnSpec:
    """A column normalized to Diagra's type spelling and default literals."""

    name: str
    data_type: str
    nullable: bool
    autoincrement: bool
    default: str | None


@dataclass(frozen=True)
class KeySpec:
    """A unique key or index; ``name`` is None when the database picks it."""

    name: str | None
    columns: tuple[str, ...]


@dataclass(frozen=True)
class ForeignKeySpec:
    """A foreign key from local columns to a referenced key."""

    columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]
    on_delete: str | None
    on_update: str | None


@dataclass(frozen=True)
class CheckSpec:
    """A named or anonymous check predicate."""

    name: str | None
    expression: str


@dataclass(frozen=True)
class TableSpec:
    """Everything the drift check compares for one table."""

    name: str
    columns: tuple[ColumnSpec, ...]
    primary_key: tuple[str, ...]
    unique_keys: frozenset[KeySpec]
    indexes: frozenset[KeySpec]
    foreign_keys: frozenset[ForeignKeySpec]
    checks: frozenset[CheckSpec]


@dataclass(frozen=True)
class SchemaSpec:
    """A normalized schema keyed by sorted table name."""

    tables: tuple[TableSpec, ...]

    def table_names(self) -> list[str]:
        """Return the table names in sorted order."""
        return [table.name for table in self.tables]

    def table(self, name: str) -> TableSpec | None:
        """Return one table by name."""
        for table in self.tables:
            if table.name == name:
                return table
        return None


@dataclass
class DiagraDocument:
    """The ERD content of the schema document's schema page."""

    tables: list[DiagraTableElement] = field(default_factory=list)
    relations: list[DiagraRelationElement] = field(default_factory=list)
    page_ids: list[str] = field(default_factory=list)


def load_diagra_document(path: Path = DIAGRA_DOCUMENT) -> DiagraDocument:
    """Parse the ERD tables and relations from a Diagra JSONL document."""
    document: DiagraDocument = DiagraDocument()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        header: DiagraRecordHeader = HEADER_ADAPTER.validate_json(line)
        if header.kind == "page" and header.id is not None:
            document.page_ids.append(header.id)
        if header.kind != "element":
            continue
        if header.type == "erd.table":
            document.tables.append(TABLE_ADAPTER.validate_json(line))
        elif header.type == "erd.relation":
            document.relations.append(RELATION_ADAPTER.validate_json(line))
    return document


def diagra_type(sql_type: TypeEngine[int] | TypeEngine[str] | TypeEngine[bool]) -> str:
    """Spell a SQLAlchemy column type the way the Diagra document does."""
    if isinstance(sql_type, Boolean):
        return "boolean"
    if isinstance(sql_type, DateTime):
        return "timestamp with time zone" if sql_type.timezone else "timestamp"
    if isinstance(sql_type, Float):
        return "double precision"
    if isinstance(sql_type, Integer):
        return "integer"
    if isinstance(sql_type, Text):
        return "text"
    if isinstance(sql_type, String):
        return f"varchar({sql_type.length})" if sql_type.length is not None else "text"
    message: str = (
        f"SQLAlchemy type {sql_type!r} has no Diagra spelling; extend diagra_type() "
        "together with the Diagra document."
    )
    raise AssertionError(message)


def _boolean_default(value: str) -> str:
    lowered: str = value.strip().lower()
    for raw, canonical in BOOLEAN_DEFAULTS:
        if lowered == raw:
            return canonical
    return lowered


def _model_default(column: Column[int] | Column[str] | Column[bool]) -> str | None:
    server_default: TextClause | str | None = None
    if column.server_default is not None:
        server_default = getattr(column.server_default, "arg", None)
    if server_default is None:
        return None
    value: str = server_default if isinstance(server_default, str) else server_default.text
    if isinstance(column.type, Boolean):
        return _boolean_default(value)
    if isinstance(column.type, Integer | Float):
        return value
    escaped: str = value.replace("'", "''")
    return f"'{escaped}'"


def _diagra_default(column: DiagraColumn) -> str | None:
    if column.default_expression is None:
        return None
    if column.data_type == "boolean":
        return _boolean_default(column.default_expression)
    return column.default_expression.strip()


def _model_table(table: Table) -> TableSpec:
    columns: tuple[ColumnSpec, ...] = tuple(
        ColumnSpec(
            name=column.name,
            data_type=diagra_type(column.type),
            nullable=bool(column.nullable) and not column.primary_key,
            autoincrement=table.autoincrement_column is column,
            default=_model_default(column),
        )
        for column in table.columns
    )
    unique_keys: set[KeySpec] = set()
    foreign_keys: set[ForeignKeySpec] = set()
    checks: set[CheckSpec] = set()
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            name: str | None = constraint.name if isinstance(constraint.name, str) else None
            unique_keys.add(KeySpec(name, tuple(column.name for column in constraint.columns)))
        elif isinstance(constraint, ForeignKeyConstraint):
            foreign_keys.add(
                ForeignKeySpec(
                    columns=tuple(column.name for column in constraint.columns),
                    referred_table=constraint.referred_table.name,
                    referred_columns=tuple(element.column.name for element in constraint.elements),
                    on_delete=constraint.ondelete.upper() if constraint.ondelete else None,
                    on_update=constraint.onupdate.upper() if constraint.onupdate else None,
                )
            )
        elif isinstance(constraint, CheckConstraint):
            check_name: str | None = constraint.name if isinstance(constraint.name, str) else None
            checks.add(CheckSpec(check_name, str(constraint.sqltext).strip()))
    indexes: set[KeySpec] = set()
    for index in table.indexes:
        index_name: str | None = index.name if isinstance(index.name, str) else None
        key: KeySpec = KeySpec(index_name, tuple(column.name for column in index.columns))
        if index.unique:
            unique_keys.add(key)
        else:
            indexes.add(key)
    return TableSpec(
        name=table.name,
        columns=columns,
        primary_key=tuple(column.name for column in table.primary_key.columns),
        unique_keys=frozenset(unique_keys),
        indexes=frozenset(indexes),
        foreign_keys=frozenset(foreign_keys),
        checks=frozenset(checks),
    )


def model_schema(metadata: MetaData) -> SchemaSpec:
    """Normalize SQLAlchemy metadata into a comparable schema."""
    tables: list[TableSpec] = [_model_table(table) for table in metadata.tables.values()]
    return SchemaSpec(tables=tuple(sorted(tables, key=lambda table: table.name)))


def _column_names(table: DiagraTable, column_ids: list[str]) -> tuple[str, ...]:
    names: list[str] = []
    for column_id in column_ids:
        match: list[str] = [column.name for column in table.columns if column.id == column_id]
        names.append(match[0] if match else f"<missing column id {column_id}>")
    return tuple(names)


def _relation_foreign_key(
    relation: DiagraRelation, tables: list[DiagraTableElement]
) -> tuple[str, ForeignKeySpec]:
    local_end: DiagraEndpoint = relation.from_ if relation.cardinality == "*:1" else relation.to
    remote_end: DiagraEndpoint = relation.to if relation.cardinality == "*:1" else relation.from_
    local: list[DiagraTable] = [t.semantic for t in tables if t.id == local_end.table]
    remote: list[DiagraTable] = [t.semantic for t in tables if t.id == remote_end.table]
    if not local or not remote or relation.cardinality == "*:*":
        message: str = f"Relation {relation} cannot be expressed as a foreign key."
        raise AssertionError(message)
    return local[0].table_name, ForeignKeySpec(
        columns=_column_names(local[0], local_end.column_ids()),
        referred_table=remote[0].table_name,
        referred_columns=_column_names(remote[0], remote_end.column_ids()),
        on_delete=_referential_action(relation.on_delete),
        on_update=_referential_action(relation.on_update),
    )


def _referential_action(action: str | None) -> str | None:
    """Spell a Diagra action (``set-null``) the way SQLAlchemy stores it."""
    if action is None or action == "no-action":
        return None
    return action.replace("-", " ").upper()


def _diagra_table(
    element: DiagraTableElement, foreign_keys: list[tuple[str, ForeignKeySpec]]
) -> TableSpec:
    table: DiagraTable = element.semantic
    columns: tuple[ColumnSpec, ...] = tuple(
        ColumnSpec(
            name=column.name,
            data_type=column.data_type,
            nullable=column.nullable and not column.pk,
            autoincrement=column.auto_increment,
            default=_diagra_default(column),
        )
        for column in table.columns
    )
    unique_keys: set[KeySpec] = set()
    indexes: set[KeySpec] = set()
    for index in table.indexes:
        key: KeySpec = KeySpec(index.name, _column_names(table, index.columns))
        if index.unique:
            unique_keys.add(key)
        else:
            indexes.add(key)
    return TableSpec(
        name=table.table_name,
        columns=columns,
        primary_key=tuple(column.name for column in table.columns if column.pk),
        unique_keys=frozenset(unique_keys),
        indexes=frozenset(indexes),
        foreign_keys=frozenset(fk for owner, fk in foreign_keys if owner == table.table_name),
        checks=frozenset(CheckSpec(check.name, check.expression.strip()) for check in table.checks),
    )


def diagra_schema(document: DiagraDocument, page_id: str = SCHEMA_PAGE_ID) -> SchemaSpec:
    """Normalize the ERD tables and relations of one Diagra page."""
    tables: list[DiagraTableElement] = [t for t in document.tables if t.page == page_id]
    foreign_keys: list[tuple[str, ForeignKeySpec]] = [
        _relation_foreign_key(relation.semantic, tables)
        for relation in document.relations
        if relation.page == page_id and not relation.semantic.logical
    ]
    specs: list[TableSpec] = [_diagra_table(element, foreign_keys) for element in tables]
    return SchemaSpec(tables=tuple(sorted(specs, key=lambda table: table.name)))


def _describe(value: ColumnSpec | KeySpec | ForeignKeySpec | CheckSpec | str) -> str:
    return json.dumps(str(value))


def _column_differences(
    table: str, designed: tuple[ColumnSpec, ...], implemented: tuple[ColumnSpec, ...]
) -> list[str]:
    differences: list[str] = []
    model_columns: list[ColumnSpec] = list(implemented)
    for column in designed:
        matches: list[ColumnSpec] = [c for c in model_columns if c.name == column.name]
        if not matches:
            differences.append(f"{table}.{column.name}: only in Diagra")
        elif matches[0] != column:
            design_text: str = _describe(column)
            model_text: str = _describe(matches[0])
            differences.append(
                f"{table}.{column.name}: Diagra {design_text} != models {model_text}"
            )
    designed_names: set[str] = {column.name for column in designed}
    differences.extend(
        f"{table}.{column.name}: only in the models"
        for column in implemented
        if column.name not in designed_names
    )
    design_order: list[str] = [column.name for column in designed]
    model_order: list[str] = [column.name for column in implemented]
    if not differences and design_order != model_order:
        differences.append(
            f"{table}: column order {design_order} in Diagra, {model_order} in models"
        )
    return differences


def schema_differences(designed: SchemaSpec, implemented: SchemaSpec) -> list[str]:
    """List every difference between the Diagra design and the models."""
    differences: list[str] = []
    designed_names: set[str] = set(designed.table_names())
    implemented_names: set[str] = set(implemented.table_names())
    differences.extend(
        f"table {name} is in Diagra but not in the models"
        for name in sorted(designed_names - implemented_names)
    )
    differences.extend(
        f"table {name} is in the models but not in Diagra"
        for name in sorted(implemented_names - designed_names)
    )
    for name in sorted(designed_names & implemented_names):
        design: TableSpec | None = designed.table(name)
        model: TableSpec | None = implemented.table(name)
        if design is None or model is None:
            continue
        differences.extend(_column_differences(name, design.columns, model.columns))
        if design.primary_key != model.primary_key:
            differences.append(
                f"{name}: primary key {design.primary_key} in Diagra, {model.primary_key} in models"
            )
        comparisons: tuple[
            tuple[
                str,
                frozenset[KeySpec] | frozenset[ForeignKeySpec] | frozenset[CheckSpec],
                frozenset[KeySpec] | frozenset[ForeignKeySpec] | frozenset[CheckSpec],
            ],
            ...,
        ] = (
            ("unique keys", design.unique_keys, model.unique_keys),
            ("indexes", design.indexes, model.indexes),
            ("foreign keys", design.foreign_keys, model.foreign_keys),
            ("checks", design.checks, model.checks),
        )
        for label, design_items, model_items in comparisons:
            only_design: list[str] = sorted(_describe(item) for item in design_items - model_items)
            only_model: list[str] = sorted(_describe(item) for item in model_items - design_items)
            if only_design or only_model:
                differences.append(
                    f"{name}: {label} differ\n  only in Diagra: {only_design}\n"
                    f"  only in models: {only_model}"
                )
    return differences


def _key_of(table: DiagraTable, column_ids: list[str]) -> bool:
    primary: list[str] = [column.id for column in table.columns if column.pk]
    if sorted(primary) == sorted(column_ids):
        return True
    return any(
        index.unique and sorted(index.columns) == sorted(column_ids) for index in table.indexes
    )


def _table_by_id(tables: list[DiagraTableElement], element_id: str) -> DiagraTable | None:
    for element in tables:
        if element.id == element_id:
            return element.semantic
    return None


def logical_reference_problems(
    document: DiagraDocument, page_id: str = SCHEMA_PAGE_ID
) -> list[str]:
    """Check every logical relation joins existing, type-compatible columns to a key."""
    tables: list[DiagraTableElement] = [t for t in document.tables if t.page == page_id]
    problems: list[str] = []
    for relation in document.relations:
        semantic: DiagraRelation = relation.semantic
        if relation.page != page_id or not semantic.logical:
            continue
        # *:1 points from the referencing table to the referenced one;
        # 1:* and 1:1 name the referenced table first.
        local_end: DiagraEndpoint = semantic.from_ if semantic.cardinality == "*:1" else semantic.to
        remote_end: DiagraEndpoint = (
            semantic.to if semantic.cardinality == "*:1" else semantic.from_
        )
        local: DiagraTable | None = _table_by_id(tables, local_end.table)
        remote: DiagraTable | None = _table_by_id(tables, remote_end.table)
        if local is None or remote is None:
            problems.append(f"{relation.id}: endpoint table is missing from the schema page")
            continue
        local_ids: list[str] = local_end.column_ids()
        remote_ids: list[str] = remote_end.column_ids()
        if not local_ids or len(local_ids) != len(remote_ids):
            problems.append(f"{relation.id}: endpoints must name the same number of columns")
            continue
        local_columns: list[DiagraColumn] = [
            c for i in local_ids for c in local.columns if c.id == i
        ]
        remote_columns: list[DiagraColumn] = [
            c for i in remote_ids for c in remote.columns if c.id == i
        ]
        if len(local_columns) != len(local_ids) or len(remote_columns) != len(remote_ids):
            problems.append(f"{relation.id}: endpoint column is missing")
            continue
        for referencing, referenced in zip(local_columns, remote_columns, strict=True):
            if referencing.data_type != referenced.data_type:
                problems.append(
                    f"{relation.id}: {local.table_name}.{referencing.name} "
                    f"({referencing.data_type}) does not match {remote.table_name}."
                    f"{referenced.name} ({referenced.data_type})"
                )
        if not _key_of(remote, remote_ids):
            problems.append(
                f"{relation.id}: {remote.table_name}"
                f"({', '.join(c.name for c in remote_columns)}) is not a primary or unique key"
            )
    return problems
