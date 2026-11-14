"""Evidence upload and retrieval API routes."""

from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from telescreen.api.common import ApiContext, read_limited_body
from telescreen.api.common.auth import Principal, require_permission
from telescreen.core.errors import TelescreenError
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import EvidenceRow
from telescreen.evidence.service import EvidenceService


@dataclass(frozen=True)
class EvidenceRouterDeps:
    """Services required by evidence endpoints."""

    ctx: ApiContext
    service: EvidenceService
    max_bytes: int


@dataclass(frozen=True)
class _EvidenceResponse:
    """Safe evidence metadata returned by the API."""

    id: str
    issue_id: str
    step_run_id: str | None
    display_filename: str
    content_type: str
    size_bytes: int
    sha256: str | None
    verdict: str
    rejection_rule_classes: tuple[str, ...]
    uploaded_by: str
    created_at: str


def build_evidence_router(deps: EvidenceRouterDeps) -> APIRouter:
    """Build the authenticated evidence endpoints."""
    router: APIRouter = APIRouter(prefix="/api/evidence", tags=["evidence"])

    @router.put("")
    async def upload(
        request: Request,
        issue_id: str,
        filename: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.EVIDENCE_UPLOAD))
        ],
        step_run_id: str | None = None,
    ) -> JSONResponse:
        """Scan the raw body before any evidence storage operation."""
        data: bytes = await read_limited_body(request, deps.max_bytes)
        try:
            row: EvidenceRow = await deps.service.upload(
                issue_id, step_run_id, filename, data, principal.actor_ref()
            )
        except TelescreenError as error:
            if error.code is not ErrorCode.EVIDENCE_REJECTED:
                raise
            rule_classes: list[str] = [value for value in error.detail.split(",") if value]
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": ErrorCode.EVIDENCE_REJECTED.value,
                        "detail": "evidence rejected",
                        "rule_classes": rule_classes,
                    }
                },
            )
        return JSONResponse(status_code=201, content=asdict(_response(row)))

    @router.get("")
    async def list_evidence(
        issue_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.EVIDENCE_READ))
        ],
        step_run_id: Annotated[str | None, Query()] = None,
    ) -> JSONResponse:
        """List accepted evidence metadata for an issue."""
        del principal
        rows: tuple[EvidenceRow, ...] = await deps.service.list(issue_id, step_run_id)
        return JSONResponse(content={"items": [asdict(_response(row)) for row in rows]})

    @router.get("/{evidence_id}/content")
    async def get_content(
        evidence_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.EVIDENCE_READ))
        ],
    ) -> Response:
        """Return accepted evidence bytes as an opaque download."""
        del principal
        content: bytes = await deps.service.content(evidence_id)
        return Response(content=content, media_type="application/octet-stream")

    @router.get("/{evidence_id}")
    async def get_metadata(
        evidence_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.EVIDENCE_READ))
        ],
    ) -> JSONResponse:
        """Return accepted evidence metadata only."""
        del principal
        return JSONResponse(content=asdict(_response(await deps.service.get(evidence_id))))

    return router


def _response(row: EvidenceRow) -> _EvidenceResponse:
    """Convert one ORM row into safe wire metadata."""
    return _EvidenceResponse(
        row.id,
        row.issue_id,
        row.step_run_id,
        row.display_filename,
        row.content_type,
        row.size_bytes,
        row.sha256,
        row.verdict,
        tuple(value for value in row.rejection_rule_classes.split(",") if value),
        row.uploaded_by,
        row.created_at.isoformat(),
    )
