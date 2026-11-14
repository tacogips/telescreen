"""Bounded, deterministic inventory row comparison across supported database dialects."""

import hashlib
import json
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Row, String, Table, cast, literal, select, tuple_
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

type Cell = str | int | float | bool | datetime | None
type TransferRow = Row[tuple[Cell, ...]]


class DigestState(Protocol):
    """The incremental hash operations used by table manifests."""

    def update(self, data: bytes, /) -> None: ...

    def hexdigest(self) -> str: ...


@dataclass(frozen=True)
class TableDigest:
    """A content manifest without exposing stored values."""

    table: str
    rows: int
    sha256: str


def database_cell(value: Cell) -> Cell:
    """Make SQLite's UTC-naive timestamps explicit before binding to PostgreSQL."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value


def encode_cells(values: tuple[Cell, ...]) -> bytes:
    """Canonicalize timezone representation, preserving scalar types and exact strings."""
    normalized: list[str | int | float | bool | None] = [
        value.replace(tzinfo=UTC).isoformat(timespec="microseconds")
        if isinstance(value, datetime) and value.tzinfo is None
        else value.astimezone(UTC).isoformat(timespec="microseconds")
        if isinstance(value, datetime)
        else value
        for value in values
    ]
    return json.dumps(
        normalized, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode()


async def rows(connection: AsyncConnection, table: Table) -> AsyncGenerator[TransferRow, None]:
    """Page binary-text primary keys without leaving PostgreSQL portals open before DDL."""
    collation: str = "C" if connection.dialect.name == "postgresql" else "BINARY"
    keys: tuple[ColumnElement[str], ...] = tuple(
        cast(column, String).collate(collation) for column in table.primary_key.columns
    )
    after: tuple[str, ...] | None = None
    while True:
        statement: Select[tuple[Cell, ...]] = select(table).order_by(*keys).limit(500)
        if after is not None:
            statement = statement.where(
                tuple_(*keys) > tuple_(*(literal(value) for value in after))
            )
        result: CursorResult[tuple[Cell, ...]] = await connection.execute(statement)
        batch: Sequence[TransferRow] = result.all()
        for row in batch:
            yield row
        if len(batch) < 500:
            return
        after = row_key(batch[-1], table)


def row_key(row: TransferRow, table: Table) -> tuple[str, ...]:
    """Use the same ordering as the binary-text SQL primary-key traversal."""
    return tuple(str(row._mapping[column.name]) for column in table.primary_key.columns)


async def digest(connection: AsyncConnection, table: Table) -> TableDigest:
    """Hash all columns and rows without loading an inventory table into memory."""
    hasher: DigestState = hashlib.sha256()
    hasher.update(encode_cells(tuple(table.c.keys())))
    count: int = 0
    async with aclosing(rows(connection, table)) as records:
        async for row in records:
            hasher.update(b"\n")
            hasher.update(encode_cells(tuple(row)))
            count += 1
    return TableDigest(table.name, count, hasher.hexdigest())


async def require_subset(
    source: AsyncConnection,
    target: AsyncConnection,
    table: Table,
    immutable: tuple[str, ...],
    *,
    monotonic: str | None = None,
    acknowledged: bool = False,
) -> None:
    """Refuse replacement by a source that lost existing identity or durable history."""
    async with (
        aclosing(rows(source, table)) as source_rows,
        aclosing(rows(target, table)) as target_rows,
    ):
        candidate: TransferRow | None = await anext(source_rows, None)
        async for existing in target_rows:
            key: tuple[str, ...] = row_key(existing, table)
            while candidate is not None and row_key(candidate, table) < key:
                candidate = await anext(source_rows, None)
            if candidate is None or row_key(candidate, table) != key:
                raise RuntimeError(f"inventory source is missing target history: {table.name}")
            if encode_cells(tuple(candidate._mapping[name] for name in immutable)) != encode_cells(
                tuple(existing._mapping[name] for name in immutable)
            ):
                raise RuntimeError(f"inventory source conflicts with target history: {table.name}")
            if monotonic is not None:
                before: Cell = existing._mapping[monotonic]
                after: Cell = candidate._mapping[monotonic]
                if not isinstance(before, int) or not isinstance(after, int) or after < before:
                    raise RuntimeError(f"inventory source would rewind progress: {table.name}")
            if (
                acknowledged
                and existing._mapping["delivered_at"] is not None
                and candidate._mapping["delivered_at"] is None
            ):
                raise RuntimeError(f"inventory source would rewind acknowledgment: {table.name}")
