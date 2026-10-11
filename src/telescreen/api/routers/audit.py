"""Audit search and integrity endpoints."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.audit.query import AuditQuery, search_audit
from telescreen.audit.verify import ChainVerification, verify_chain
from telescreen.core.permissions import Permission
from telescreen.search.backend import SearchBackend, SearchDocument, SearchPage


@dataclass(frozen=True)
class AuditRouterDeps:
    """Services required by audit endpoints."""

    ctx: ApiContext
    backend: SearchBackend


def _field(document: SearchDocument, name: str) -> str | int | float | bool | None:
    """Return a scalar field or null for a non-scalar value."""
    value: str | int | float | bool | tuple[str, ...] | None = document.get(name)
    return value if isinstance(value, str | int | float | bool) or value is None else None


def build_audit_router(deps: AuditRouterDeps) -> APIRouter:
    """Build read-scoped audit routes."""
    router: APIRouter = APIRouter()

    @router.get("/api/audit/search")
    async def audit_search(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.AUDIT_READ))
        ],
        q: str = "",
        action: str | None = None,
        actor: str | None = None,
        target_kind: str | None = None,
        from_: Annotated[datetime | None, Query(alias="from")] = None,
        to: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> JSONResponse:
        """Return matching indexed audit records without raw values."""
        del principal
        safe_limit: int = min(max(limit, 1), 200)
        safe_offset: int = max(offset, 0)
        page: SearchPage = await search_audit(
            deps.backend,
            AuditQuery(q, action, actor, target_kind, from_, to, safe_limit, safe_offset),
        )
        return JSONResponse(
            content={
                "items": [
                    {
                        "id": hit.id,
                        "seq": _field(hit, "seq"),
                        "at": _field(hit, "at"),
                        "actor_kind": _field(hit, "actor_kind"),
                        "actor_ref": _field(hit, "actor_ref"),
                        "action": _field(hit, "action"),
                        "target_kind": _field(hit, "target_kind"),
                        "target_id": _field(hit, "target_id"),
                        "summary": _field(hit, "summary"),
                    }
                    for hit in page.hits
                ],
                "total": page.total,
            }
        )

    @router.get("/api/audit/verify")
    async def audit_verify(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.AUDIT_READ))
        ],
    ) -> JSONResponse:
        """Verify the append-only chain and return its first broken sequence."""
        del principal
        async with deps.ctx.sessions() as session:
            result: ChainVerification = await verify_chain(session)
        return JSONResponse(
            content={
                "ok": result.ok,
                "first_broken_seq": result.first_broken_seq,
                "checked": result.checked,
            }
        )

    return router
