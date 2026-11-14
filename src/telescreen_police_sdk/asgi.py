"""Pure ASGI adapter for the framework-neutral Police webhook handler."""

from __future__ import annotations

from typing import Literal, Protocol, overload

from telescreen_police_sdk.webhook import WebhookHandler, WebhookReply, WebhookRequest


class Scope(Protocol):
    """The ASGI scope keys consumed by this adapter."""

    @overload
    def __getitem__(self, key: Literal["type"], /) -> str: ...

    @overload
    def __getitem__(self, key: Literal["method", "path"], /) -> str: ...

    @overload
    def __getitem__(self, key: Literal["headers"], /) -> tuple[tuple[bytes, bytes], ...]: ...


class IncomingMessage(Protocol):
    """Incoming ASGI message keys consumed by the adapter."""

    def __getitem__(self, key: str, /) -> str | bytes | bool: ...


class Receive(Protocol):
    """ASGI receive callable."""

    async def __call__(self) -> IncomingMessage: ...


class Send(Protocol):
    """ASGI send callable."""

    async def __call__[Message](self, message: Message) -> None: ...


class AsgiApp(Protocol):
    """ASGI application protocol used by the SDK."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None: ...


def create_webhook_app(handler: WebhookHandler) -> AsgiApp:
    """Adapt a webhook handler to a POST-only ASGI application."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        if scope["method"].upper() != "POST":
            await send(
                {
                    "type": "http.response.start",
                    "status": 405,
                    "headers": ((b"content-type", b"text/plain"),),
                }
            )
            await send({"type": "http.response.body", "body": b"method not allowed"})
            return
        body_parts: list[bytes] = []
        more_body: bool = True
        while more_body:
            message: IncomingMessage = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk: str | bytes | bool = message["body"]
            if not isinstance(chunk, bytes):
                return
            body_parts.append(chunk)
            more: str | bytes | bool = message["more_body"]
            more_body = more if isinstance(more, bool) else False
        raw_body: bytes = b"".join(body_parts)
        headers: tuple[tuple[str, str], ...] = tuple(
            (name.decode("latin-1"), value.decode("latin-1")) for name, value in scope["headers"]
        )
        reply: WebhookReply = await handler.handle(WebhookRequest(headers, raw_body))
        response_headers: tuple[tuple[bytes, bytes], ...] = tuple(
            (name.encode("latin-1"), value.encode("latin-1")) for name, value in reply.headers
        )
        await send(
            {"type": "http.response.start", "status": reply.status, "headers": response_headers}
        )
        await send({"type": "http.response.body", "body": reply.body})

    return app
