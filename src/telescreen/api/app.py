"""FastAPI application assembly and optional built UI serving."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from telescreen.api.common.errors import install_error_handlers
from telescreen.core.errors import ConfigError
from telescreen.wiring import Container, close_container


def _web_root(container: Container) -> Path | None:
    """Resolve and validate the optional static UI directory."""
    configured: str | None = container.settings.web_dist
    if configured is None:
        return None
    root: Path = Path(configured)
    if not root.is_absolute():
        root = Path.cwd() / root
    resolved: Path = root.resolve()
    if not (resolved / "index.html").is_file():
        raise ConfigError("configured web directory is unavailable")
    return resolved


def create_app(container: Container) -> FastAPI:
    """Build the Reporting Bureau API with a safe static UI fallback."""
    web_root: Path | None = _web_root(container)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Initialize search indexes and release container resources on shutdown."""
        del app
        try:
            await container.ensure_indexes()
            yield
        finally:
            await close_container(container)

    app: FastAPI = FastAPI(title="Telescreen Reporting Bureau", lifespan=lifespan)
    install_error_handlers(app)
    for router in container.routers:
        app.include_router(router)

    if web_root is not None:

        async def static_or_spa(request: Request) -> Response:
            """Serve a safe file or the SPA shell, excluding API paths."""
            requested: str = request.path_params.get("path", "")
            if requested == "api" or requested.startswith("api/"):
                return JSONResponse(
                    status_code=404,
                    content={"error": {"code": "not_found", "detail": "not found"}},
                )
            candidate: Path = (web_root / requested).resolve()
            if candidate.is_relative_to(web_root) and candidate.is_file():
                return FileResponse(candidate)
            if request.method not in ("GET", "HEAD"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            return FileResponse(web_root / "index.html", headers={"Cache-Control": "no-cache"})

        app.router.routes.append(
            Route(
                "/{path:path}",
                static_or_spa,
                methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
            )
        )
    return app
