"""Framework-neutral signed push-mode webhook handler."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from telescreen_police_sdk import _json
from telescreen_police_sdk.client import BureauClient
from telescreen_police_sdk.models import (
    Callback,
    ResultEnvelope,
    ResultError,
    ResultStatus,
    TaskEnvelope,
)
from telescreen_police_sdk.signing import NonceCache, VerifyResult, verify
from telescreen_police_sdk.worker import TaskHandler


class WebhookMode(StrEnum):
    """Push response mode."""

    SYNC = "sync"
    ASYNC = "async"


@dataclass(frozen=True)
class WebhookRequest:
    """Raw signed request received by the webhook."""

    headers: tuple[tuple[str, str], ...]
    body: bytes = field(repr=False)


@dataclass(frozen=True)
class WebhookReply:
    """HTTP reply returned by the framework-neutral handler."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes = field(repr=False)


@dataclass
class WebhookHandler:
    """Verify task signatures and run sync or asynchronous result callbacks."""

    handlers: tuple[tuple[str, TaskHandler], ...]
    secret: bytes = field(repr=False)
    mode: WebhookMode = WebhookMode.SYNC
    client: BureauClient | None = None
    clock: Callable[[], int] = lambda: int(time.time())
    nonce_cache: NonceCache = field(default_factory=NonceCache, repr=False)
    _background: set[asyncio.Task[None]] = field(default_factory=set, init=False, repr=False)

    async def handle(self, request: WebhookRequest) -> WebhookReply:
        """Authenticate and process a signed task request."""
        verdict: VerifyResult = verify(
            request.headers, request.body, self.secret, self.clock(), self.nonce_cache
        )
        if verdict is not VerifyResult.OK:
            return self._reply(401, b'{"error":"unauthorized"}')
        try:
            envelope: TaskEnvelope = TaskEnvelope.from_json(_json.parse(request.body))
        except (ValueError, TypeError):
            return self._reply(400, b'{"error":"invalid_request"}')
        handler: TaskHandler | None = next(
            (candidate for name, candidate in self.handlers if name == envelope.task_type), None
        )
        if handler is None:
            return self._reply(400, b'{"error":"unsupported_task"}')
        if self.mode is WebhookMode.SYNC:
            result: ResultEnvelope = await self._run_handler(handler, envelope)
            return self._reply(200, _json.dump(result.to_json()).encode())
        if envelope.callback is None or self.client is None:
            return self._reply(400, b'{"error":"callback_required"}')
        background: asyncio.Task[None] = asyncio.create_task(
            self._run_and_callback(handler, envelope)
        )
        self._background.add(background)
        background.add_done_callback(self._background.discard)
        return self._reply(202, b'{"accepted":true}')

    async def _run_and_callback(self, handler: TaskHandler, envelope: TaskEnvelope) -> None:
        """Run task and post a signed one-time result callback."""
        callback: Callback | None = envelope.callback
        if callback is None or self.client is None:
            return
        result: ResultEnvelope = await self._run_handler(handler, envelope)
        callback_result: ResultEnvelope = ResultEnvelope(
            task_id=result.task_id,
            status=result.status,
            outcome=result.outcome,
            summary=result.summary,
            findings=result.findings,
            payload=result.payload,
            error=result.error,
            result_token=callback.result_token,
            stats=result.stats,
            complete_locators=result.complete_locators,
            contract_version=result.contract_version,
        )
        await self.client.callback(callback.url, callback_result, self.secret)

    @staticmethod
    async def _run_handler(handler: TaskHandler, envelope: TaskEnvelope) -> ResultEnvelope:
        """Return a handler result, hiding exception details from the wire."""
        try:
            return await handler(envelope)
        except Exception:
            return ResultEnvelope(
                envelope.task_id,
                ResultStatus.FAILED,
                error=ResultError("handler_error", "Task handler failed"),
            )

    @staticmethod
    def _reply(status: int, body: bytes) -> WebhookReply:
        """Construct a JSON response with no request-derived text."""
        return WebhookReply(status, (("content-type", "application/json"),), body)
