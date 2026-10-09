"""Unauthenticated health endpoint."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse


def build_health_router() -> APIRouter:
    """Build the health endpoint router."""
    router: APIRouter = APIRouter()

    @router.get("/api/health")
    async def health() -> JSONResponse:
        """Return the service health marker."""
        return JSONResponse(content={"status": "ok"})

    return router
