"""Global masked search endpoint."""

from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import Principal, require_scope
from telescreen.api.common.context import ApiContext
from telescreen.core.vocabulary import ApiScope
from telescreen.search.backend import (
    FieldValue,
    IndexName,
    SearchBackend,
    SearchDocument,
    SearchPage,
    SearchQuery,
)


@dataclass(frozen=True)
class SearchRouterDeps:
    """Services required by the global search endpoint."""

    ctx: ApiContext
    backend: SearchBackend


@dataclass(frozen=True)
class SearchHit:
    """Stable UI search result fields."""

    index: str
    id: str
    title: str
    snippet: str | None
    status: str | None
    at: str | None


def _value(document: SearchDocument, name: str) -> str | None:
    """Read a string field from an indexed document."""
    value: FieldValue | tuple[str, ...] | None = document.get(name)
    return str(value) if isinstance(value, str | int | float | bool) else None


def _hit(index: IndexName, document: SearchDocument) -> SearchHit:
    """Map a document into the stable UI search-hit shape."""
    if index is IndexName.ISSUES:
        title: str = (
            f"{_value(document, 'short_id') or ''} {_value(document, 'display_title') or ''}"
        ).strip()
        snippet: str | None = _value(document, "display_locator")
        status: str | None = _value(document, "status")
        at: str | None = _value(document, "last_seen_ts")
    elif index is IndexName.FINDINGS:
        title = _value(document, "rule_id") or ""
        snippet = _value(document, "masked_snippet")
        status = None
        at = _value(document, "observed_at_ts")
    elif index is IndexName.RUNS:
        title = _value(document, "workflow") or ""
        snippet = _value(document, "issue_id")
        status = _value(document, "status")
        at = _value(document, "started_at_ts")
    else:
        title = _value(document, "action") or ""
        snippet = _value(document, "summary")
        status = None
        at = _value(document, "at")
    return SearchHit(index.value, document.id, title, snippet, status, at)


def build_search_router(deps: SearchRouterDeps) -> APIRouter:
    """Build the read-scoped global search route."""
    router: APIRouter = APIRouter()

    @router.get("/api/search")
    async def search(
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
        q: str = "",
        index: IndexName = IndexName.ISSUES,
        limit: int = 20,
    ) -> JSONResponse:
        """Return masked search hits from one configured index."""
        del principal
        page: SearchPage = await deps.backend.search(
            index, SearchQuery(q, limit=min(max(limit, 1), 200))
        )
        return JSONResponse(
            content={"items": [asdict(_hit(index, hit)) for hit in page.hits], "total": page.total}
        )

    return router
