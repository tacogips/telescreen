"""Bureau-owned internal endpoints and public inventory API forwarding."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import TypeAdapter, ValidationError
from starlette.responses import Response, StreamingResponse

from telescreen.api.common.auth import authenticate
from telescreen.api.common.context import ApiContext
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.inventory_service.transport import (
    PREFIX,
    InternalAuth,
    ServiceHttp,
    encode_response,
    read_payload,
)
from telescreen.ports.asset_context import AssetIssueLookup, ResourceIssueRef
from telescreen.ports.audit_commands import AuditReceipt, AuditReceiver, decode_audit_command
from telescreen.ports.identity_context import ActorDisplay, ActorLookup, AssigneeIds, AssigneeLookup
from telescreen.ports.principal import Principal
from telescreen.ports.retention import RetentionReceipt, RetentionReceiver, decode_retention_command


@dataclass(frozen=True)
class BureauEndpoints:
    """Only bureau-owned contracts needed by the independent inventory service."""

    context: ApiContext
    issues: AssetIssueLookup
    actors: ActorLookup
    assignees: AssigneeLookup
    audit: AuditReceiver
    retention: RetentionReceiver


async def decode_body[T](request: Request, adapter: TypeAdapter[T]) -> T:
    """Hide validation payloads and keep the transport bounded."""
    try:
        return adapter.validate_json(await read_payload(request), strict=True)
    except ValidationError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid service request") from None


def build_bureau_router(deps: BureauEndpoints, auth: InternalAuth) -> APIRouter:
    """Expose only versioned owner operations under service authentication."""
    router: APIRouter = APIRouter(prefix=PREFIX, dependencies=[Depends(auth)])

    @router.post("/authenticate")
    async def principal(request: Request) -> Response:
        """Resolve current API-token revocation and OIDC/member state."""
        return encode_response(await authenticate(deps.context, request), TypeAdapter(Principal))

    @router.post("/issues")
    async def issues(request: Request) -> Response:
        """Batch safe references using an issue-owned transaction."""
        return encode_response(
            await deps.issues.open_issues_for_resources(
                await decode_body(request, TypeAdapter(tuple[str, ...]))
            ),
            TypeAdapter(tuple[ResourceIssueRef, ...]),
        )

    @router.post("/actors")
    async def actors(request: Request) -> Response:
        """Resolve ordered detached actor displays."""
        return encode_response(
            await deps.actors.display(await decode_body(request, TypeAdapter(tuple[str, ...]))),
            TypeAdapter(tuple[ActorDisplay, ...]),
        )

    @router.post("/assignee")
    async def assignee(request: Request) -> Response:
        """Validate current member/team references."""
        return encode_response(
            await deps.assignees.assignee_of(await decode_body(request, TypeAdapter(str))),
            TypeAdapter(AssigneeIds),
        )

    @router.post("/audit")
    async def audit(request: Request) -> Response:
        """Return a receipt only after audit commit."""
        return encode_response(
            await deps.audit.receive(decode_audit_command(await read_payload(request))),
            TypeAdapter(AuditReceipt),
        )

    @router.post("/retention")
    async def retention(request: Request) -> Response:
        """Apply current notification policy and commit deduplication locally."""
        return encode_response(
            await deps.retention.receive(decode_retention_command(await read_payload(request))),
            TypeAdapter(RetentionReceipt),
        )

    return router


def build_proxy_router(transport: ServiceHttp) -> APIRouter:
    """Retain public URLs and forward only relevant headers to the fixed service."""
    router: APIRouter = APIRouter()

    @router.api_route(
        "/api/data/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]
    )
    async def proxy(request: Request, path: str) -> Response:
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

    return router
