"""Bureau-owned internal endpoints and public inventory API forwarding."""

from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Request
from starlette.responses import Response, StreamingResponse

from telescreen.inventory_service.transport import ServiceHttp, read_payload

INVENTORY_PROXY_PATHS: tuple[str, ...] = ("/api/data/{path:path}",)


def build_proxy_router(
    transport: ServiceHttp, paths: tuple[str, ...] = INVENTORY_PROXY_PATHS
) -> APIRouter:
    """Retain public URLs and forward only relevant headers to the fixed service."""
    router: APIRouter = APIRouter()

    async def proxy(request: Request, path: str | None = None) -> Response:
        """Keep upstream status, CSV disposition and content type without cookies."""
        del path
        response: httpx.Response = await transport.open_stream(
            request.method,
            request.url.path + ("?" + request.url.query if request.url.query else ""),
            (await read_payload(request)).encode(),
            request.headers.get("authorization", ""),
        )

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()

        return StreamingResponse(
            body(),
            status_code=response.status_code,
            headers={
                name: value
                for name, value in response.headers.items()
                if name in ("content-type", "content-disposition", "cache-control")
            },
        )

    for path_pattern in paths:
        router.add_api_route(
            path_pattern,
            proxy,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
        )
    return router
