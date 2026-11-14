"""Meilisearch HTTP backend implemented through httpx."""

from dataclasses import dataclass

import httpx

from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_array,
    json_int,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.secrets import SecretValue
from telescreen.search.backend import (
    FieldValue,
    IndexName,
    SearchBackend,
    SearchDocument,
    SearchPage,
    SearchQuery,
)
from telescreen.search.settings import INDEX_SETTINGS


def _escape_filter(value: str) -> str:
    """Quote and escape one string literal in a Meilisearch filter."""
    escaped: str = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _document_json(document: SearchDocument) -> JsonValue:
    """Convert one typed document to the JSON boundary model."""
    values: list[tuple[str, JsonValue]] = [("id", json_str(document.id))]
    for name, value in document.fields:
        if isinstance(value, tuple):
            encoded: JsonValue = json_array(tuple(json_str(item) for item in value))
        elif value is None:
            encoded = parse_json("null")
        elif isinstance(value, bool):
            encoded = parse_json("true" if value else "false")
        elif isinstance(value, int):
            encoded = json_int(value)
        elif isinstance(value, float):
            encoded = parse_json(str(value))
        else:
            encoded = json_str(value)
        values.append((name, encoded))
    return json_object(values)


def _field_value(value: JsonValue) -> FieldValue | tuple[str, ...]:
    """Read an allowed Meilisearch scalar value."""
    if value.kind is JsonKind.STRING:
        return value.str_value
    if value.kind is JsonKind.INT:
        return value.int_value
    if value.kind is JsonKind.FLOAT:
        return value.float_value
    if value.kind is JsonKind.BOOL:
        return value.bool_value
    if value.kind is JsonKind.NULL:
        return None
    if value.kind is JsonKind.ARRAY:
        return tuple(item.str_value for item in value.items if item.kind is JsonKind.STRING)
    return None


def _document(value: JsonValue) -> SearchDocument | None:
    """Convert a result object into a safe typed document."""
    if value.kind is not JsonKind.OBJECT:
        return None
    identifier: JsonValue | None = value.get("id")
    if identifier is None or identifier.as_str() is None:
        return None
    fields: list[tuple[str, FieldValue | tuple[str, ...]]] = []
    for name in value.member_names():
        if name != "id":
            field_value: JsonValue | None = value.get(name)
            if field_value is not None:
                fields.append((name, _field_value(field_value)))
    return SearchDocument(identifier.str_value, tuple(fields))


@dataclass(frozen=True)
class MeiliSearchBackend(SearchBackend):
    """Meilisearch backend with explicit JSON serialization and typed responses."""

    http: httpx.AsyncClient
    base_url: str
    api_key: SecretValue | None = None

    def _headers(self) -> tuple[tuple[str, str], ...]:
        """Return authorization headers without logging secret material."""
        return (("Authorization", f"Bearer {self.api_key.value}"),) if self.api_key else ()

    async def ensure_indexes(self) -> None:
        """Create known indexes and apply filter and sort settings."""
        for index, settings in INDEX_SETTINGS:
            response: httpx.Response = await self.http.post(
                f"{self.base_url.rstrip('/')}/indexes",
                headers=self._headers(),
                content=dump_json(
                    json_object((("uid", json_str(index.value)), ("primaryKey", json_str("id"))))
                ),
            )
            if response.status_code not in (201, 202, 409):
                response.raise_for_status()
            settings_json: JsonValue = json_object(
                (
                    (
                        "filterableAttributes",
                        json_array(tuple(json_str(name) for name in settings.filterable)),
                    ),
                    (
                        "sortableAttributes",
                        json_array(tuple(json_str(name) for name in settings.sortable)),
                    ),
                )
            )
            await self.http.put(
                f"{self.base_url.rstrip('/')}/indexes/{index.value}/settings",
                headers=self._headers(),
                content=dump_json(settings_json),
            )

    async def upsert(self, index: IndexName, documents: tuple[SearchDocument, ...]) -> None:
        """Submit documents using their stable id primary key."""
        payload: JsonValue = json_array(tuple(_document_json(document) for document in documents))
        response: httpx.Response = await self.http.post(
            f"{self.base_url.rstrip('/')}/indexes/{index.value}/documents",
            headers=self._headers(),
            content=dump_json(payload),
        )
        response.raise_for_status()

    async def delete(self, index: IndexName, ids: tuple[str, ...]) -> None:
        """Delete a batch of document ids."""
        response: httpx.Response = await self.http.post(
            f"{self.base_url.rstrip('/')}/indexes/{index.value}/documents/delete-batch",
            headers=self._headers(),
            content=dump_json(json_array(tuple(json_str(item) for item in ids))),
        )
        response.raise_for_status()

    async def search(self, index: IndexName, query: SearchQuery) -> SearchPage:
        """Search one index and parse only supported scalar fields."""
        filters: tuple[JsonValue, ...] = tuple(
            json_str(f"{item.field} {item.op.value} {_escape_filter(str(item.value))}")
            for item in query.filters
        )
        payload: JsonValue = json_object(
            (
                ("q", json_str(query.q)),
                ("limit", json_int(query.limit)),
                ("offset", json_int(query.offset)),
                ("filter", json_array(filters)),
                ("sort", json_array(tuple(json_str(item) for item in query.sort))),
            )
        )
        response: httpx.Response = await self.http.post(
            f"{self.base_url.rstrip('/')}/indexes/{index.value}/search",
            headers=self._headers(),
            content=dump_json(payload),
        )
        response.raise_for_status()
        result: JsonValue = parse_json(response.content)
        hits_value: JsonValue | None = result.get("hits")
        total_value: JsonValue | None = result.get("estimatedTotalHits")
        if hits_value is None or hits_value.as_list() is None:
            return SearchPage((), 0)
        hits: list[SearchDocument] = [
            document for item in hits_value.items if (document := _document(item)) is not None
        ]
        total: int = len(hits)
        if total_value is not None:
            parsed_total: int | None = total_value.as_int()
            if parsed_total is not None:
                total = parsed_total
        return SearchPage(tuple(hits), total)
