"""Searchable audit query model and backend adapter."""

from dataclasses import dataclass
from datetime import datetime

from telescreen.search.backend import (
    FilterOp,
    IndexName,
    SearchBackend,
    SearchFilter,
    SearchPage,
    SearchQuery,
)


@dataclass(frozen=True)
class AuditQuery:
    """Optional text and structured audit filters with a bounded page."""

    q: str
    action: str | None
    actor: str | None
    target_kind: str | None
    start: datetime | None
    end: datetime | None
    limit: int
    offset: int


async def search_audit(backend: SearchBackend, query: AuditQuery) -> SearchPage:
    """Search audit documents using the configured audit index."""
    filters: list[SearchFilter] = []
    if query.action is not None:
        filters.append(SearchFilter("action", FilterOp.EQ, query.action))
    if query.actor is not None:
        filters.append(SearchFilter("actor_ref", FilterOp.EQ, query.actor))
    if query.target_kind is not None:
        filters.append(SearchFilter("target_kind", FilterOp.EQ, query.target_kind))
    if query.start is not None:
        filters.append(SearchFilter("at_ts", FilterOp.GTE, int(query.start.timestamp())))
    if query.end is not None:
        filters.append(SearchFilter("at_ts", FilterOp.LTE, int(query.end.timestamp())))
    return await backend.search(
        IndexName.AUDIT,
        SearchQuery(query.q, tuple(filters), ("seq:desc",), query.limit, query.offset),
    )
