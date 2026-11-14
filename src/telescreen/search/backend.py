"""Typed search backend contract and transport-neutral value objects."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

type FieldValue = str | int | float | bool | None
type SearchField = tuple[str, FieldValue | tuple[str, ...]]


@dataclass(frozen=True)
class SearchDocument:
    """One indexed document with closed scalar fields."""

    id: str
    fields: tuple[SearchField, ...]

    def get(self, name: str) -> FieldValue | tuple[str, ...] | None:
        """Return a field value by name."""
        return next((value for key, value in self.fields if key == name), None)


class FilterOp(StrEnum):
    """Supported search filter comparisons."""

    EQ = "="
    GTE = ">="
    LTE = "<="


@dataclass(frozen=True)
class SearchFilter:
    """One typed search constraint."""

    field: str
    op: FilterOp
    value: str | int


@dataclass(frozen=True)
class SearchQuery:
    """Search text, filters, ordering and bounded page window."""

    q: str
    filters: tuple[SearchFilter, ...] = ()
    sort: tuple[str, ...] = ()
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class SearchPage:
    """Search results and total hit count."""

    hits: tuple[SearchDocument, ...]
    total: int


class IndexName(StrEnum):
    """Supported search index names."""

    ISSUES = "issues"
    FINDINGS = "findings"
    RUNS = "runs"
    AUDIT = "audit"


class SearchBackend(Protocol):
    """Index documents and search through a replaceable backend."""

    async def ensure_indexes(self) -> None:
        """Create indexes and apply their settings."""
        ...

    async def upsert(self, index: IndexName, documents: tuple[SearchDocument, ...]) -> None:
        """Insert or replace documents."""
        ...

    async def delete(self, index: IndexName, ids: tuple[str, ...]) -> None:
        """Delete documents by primary key."""
        ...

    async def search(self, index: IndexName, query: SearchQuery) -> SearchPage:
        """Search one index."""
        ...
