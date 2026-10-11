"""Unauthenticated health endpoint."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import declare_external
from telescreen.core.vocabulary import ExternalAuthKind


def build_health_router() -> APIRouter:
    """Build the health endpoint router."""
    router: APIRouter = APIRouter()

    @router.get("/api/health", dependencies=[Depends(declare_external(ExternalAuthKind.PUBLIC))])
    async def health() -> JSONResponse:
        """Return the service health marker."""
        return JSONResponse(content={"status": "ok"})

    return router
