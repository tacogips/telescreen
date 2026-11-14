"""Search backends and safe document indexing."""

from telescreen.search.backend import (
    FilterOp,
    IndexName,
    SearchDocument,
    SearchFilter,
    SearchPage,
    SearchQuery,
)
from telescreen.search.memory import InMemorySearchBackend

__all__: list[str] = [
    "FilterOp",
    "IndexName",
    "InMemorySearchBackend",
    "SearchDocument",
    "SearchFilter",
    "SearchPage",
    "SearchQuery",
]
