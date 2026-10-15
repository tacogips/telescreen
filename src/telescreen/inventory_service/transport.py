"""Authenticated, bounded HTTP boundary shared by the two service owners."""

import hmac
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit

import httpx
from fastapi import Request
from pydantic import TypeAdapter, ValidationError
from starlette.responses import Response

from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import ErrorCode
from telescreen.ports.handoff_limits import MAX_HANDOFF_BYTES

PREFIX: str = "/internal/inventory/v1"
NOTIFICATION_PREFIX: str = "/internal/notification/v1"
SERVICE_HEADER: str = "x-telescreen-service-token"


@dataclass(frozen=True)
class RemoteError:
    """Only the stable error code is interpreted across the service boundary."""

    code: ErrorCode
    detail: str = field(repr=False)


@dataclass(frozen=True)
class ErrorEnvelope:
    """Standard safe API error response."""

    error: RemoteError


@dataclass(frozen=True)
class InternalAuth:
    """Require a service secret independently of end-user authentication."""

    secret: SecretValue

    def check(self, request: Request) -> None:
        """Reject missing or invalid credentials without revealing configured values."""
        supplied: str = request.headers.get(SERVICE_HEADER, "")
        if not hmac.compare_digest(supplied.encode(), self.secret.value.encode()):
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "service authentication required")

    async def __call__(self, request: Request) -> None:
        """FastAPI dependency entry point."""
        self.check(request)


@dataclass(frozen=True)
class ServiceHttp:
    """Use a caller-owned connection pool, fixed origin and finite request timeout."""

    http: httpx.AsyncClient
    base_url: str
    secret: SecretValue
    prefix: str = PREFIX

    def __post_init__(self) -> None:
        """Reject ambiguous destinations and empty service credentials at startup."""
        try:
            parsed: SplitResult = urlsplit(self.base_url)
            port: int | None = parsed.port
        except ValueError:
            raise ConfigError("service URL must be an HTTP origin") from None
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or port == 0
        ):
            raise ConfigError("service URL must be an HTTP origin")
        if not self.secret.value.strip():
            raise ConfigError("inventory service token must not be empty")

    async def open_stream(
        self, method: str, path: str, payload: bytes = b"", authorization: str = ""
    ) -> httpx.Response:
        """Open a caller-closed response without redirects or an unbounded timeout."""
        request: httpx.Request = self.http.build_request(
            method,
            self.base_url.rstrip("/") + path,
            content=payload,
            headers={
                SERVICE_HEADER: self.secret.value,
                "authorization": authorization,
                "content-type": "application/json",
            },
            timeout=30.0,
        )
        try:
            return await self.http.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError:
            raise TelescreenError(ErrorCode.SERVICE_UNAVAILABLE, "service unavailable") from None

    async def request(
        self, method: str, path: str, payload: bytes = b"", authorization: str = ""
    ) -> httpx.Response:
        """Bound internal responses before decoding or acknowledging durable work."""
        response: httpx.Response = await self.open_stream(method, path, payload, authorization)
        try:
            chunks: list[bytes] = []
            size: int = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_HANDOFF_BYTES:
                    raise TelescreenError(
                        ErrorCode.SERVICE_UNAVAILABLE, "service response exceeds limit"
                    )
                chunks.append(chunk)
            return httpx.Response(
                response.status_code,
                headers={
                    name: value
                    for name, value in response.headers.items()
                    if name not in ("content-encoding", "content-length")
                },
                content=b"".join(chunks),
            )
        except httpx.HTTPError:
            raise TelescreenError(ErrorCode.SERVICE_UNAVAILABLE, "service unavailable") from None
        finally:
            await response.aclose()

    async def post[T](
        self, path: str, payload: str, result: TypeAdapter[T], authorization: str = ""
    ) -> T:
        """Validate response shape before a caller may acknowledge durable work."""
        response: httpx.Response = await self.request(
            "POST", self.prefix + path, payload.encode(), authorization
        )
        if response.status_code != 200:
            try:
                failure: ErrorEnvelope = TypeAdapter(ErrorEnvelope).validate_json(response.content)
            except ValidationError:
                raise TelescreenError(
                    ErrorCode.SERVICE_UNAVAILABLE, "service unavailable"
                ) from None
            raise TelescreenError(failure.error.code, "service request rejected")
        try:
            return result.validate_json(response.content, strict=True)
        except ValidationError:
            raise TelescreenError(
                ErrorCode.SERVICE_UNAVAILABLE, "invalid service response"
            ) from None


async def read_payload(request: Request) -> str:
    """Bound inbound streams before decoding or validating application data."""
    chunks: list[bytes] = []
    size: int = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_HANDOFF_BYTES:
            raise TelescreenError(ErrorCode.PAYLOAD_TOO_LARGE, "request exceeds limit")
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid request encoding") from None


def encode_response[T](value: T, adapter: TypeAdapter[T]) -> Response:
    """Serialize detached typed values without an intermediate untyped mapping."""
    return Response(adapter.dump_json(value), media_type="application/json")
