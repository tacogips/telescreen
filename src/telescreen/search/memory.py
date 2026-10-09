"""Non-durable in-memory search backend for development and tests."""

from dataclasses import dataclass, field

from telescreen.search.backend import (
    FieldValue,
    IndexName,
    SearchBackend,
    SearchDocument,
    SearchFilter,
    SearchPage,
    SearchQuery,
)


def _matches(document: SearchDocument, constraint: SearchFilter) -> bool:
    """Evaluate one filter against a stored scalar value."""
    value: FieldValue | tuple[str, ...] | None = document.get(constraint.field)
    if isinstance(value, tuple):
        return str(constraint.value) in value if constraint.op.value == "=" else False
    if value is None:
        return False
    if constraint.op.value == "=":
        return value == constraint.value
    try:
        number: float = float(value)
        bound: float = float(constraint.value)
    except (TypeError, ValueError):
        return False
    return number >= bound if constraint.op.value == ">=" else number <= bound


def _sort_value(document: SearchDocument, field_name: str) -> tuple[int, float | str]:
    """Keep numeric fields numeric while giving other values stable string ordering."""
    value: FieldValue | tuple[str, ...] | None = document.get(field_name)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, float(value))
    return (1, str(value or "").casefold())


@dataclass
class InMemorySearchBackend(SearchBackend):
    """Simple case-insensitive substring search over indexed fields."""

    documents: list[tuple[IndexName, SearchDocument]] = field(default_factory=list)

    async def ensure_indexes(self) -> None:
        """Initialize empty index storage."""
        self.documents = [(index, document) for index, document in self.documents]

    async def upsert(self, index: IndexName, documents: tuple[SearchDocument, ...]) -> None:
        """Store or replace documents by id."""
        for document in documents:
            self.documents = [
                (existing_index, existing)
                for existing_index, existing in self.documents
                if existing_index is not index or existing.id != document.id
            ]
            self.documents.append((index, document))

    async def delete(self, index: IndexName, ids: tuple[str, ...]) -> None:
        """Remove documents if present."""
        self.documents = [
            (existing_index, existing)
            for existing_index, existing in self.documents
            if existing_index is not index or existing.id not in ids
        ]

    async def search(self, index: IndexName, query: SearchQuery) -> SearchPage:
        """Search string fields, then apply filters, sorting and pagination."""
        needle: str = query.q.casefold()
        matches: list[SearchDocument] = []
        for stored_index, document in self.documents:
            if stored_index is not index:
                continue
            text_match: bool = not needle or any(
                needle in value.casefold()
                for _, field_value in document.fields
                for value in (
                    (field_value,)
                    if isinstance(field_value, str)
                    else field_value
                    if isinstance(field_value, tuple)
                    else ()
                )
            )
            if text_match and all(_matches(document, constraint) for constraint in query.filters):
                matches.append(document)
        for sort_key in reversed(query.sort):
            field_name: str = sort_key.removesuffix(":desc").removesuffix(":asc")
            descending: bool = sort_key.endswith(":desc")
            matches.sort(key=lambda doc: _sort_value(doc, field_name), reverse=descending)
        total: int = len(matches)
        return SearchPage(tuple(matches[query.offset : query.offset + query.limit]), total)
