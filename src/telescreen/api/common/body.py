"""Bounded request body reading helpers."""

from fastapi import Request

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode


async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    """Read a streamed body with at most one byte beyond the configured limit."""
    if max_bytes < 0:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid body limit")
    body: bytearray = bytearray()
    async for chunk in request.stream():
        remaining: int = max_bytes + 1 - len(body)
        body.extend(chunk[:remaining])
        if len(body) > max_bytes:
            raise TelescreenError(ErrorCode.PAYLOAD_TOO_LARGE, "request body is too large")
    return bytes(body)
