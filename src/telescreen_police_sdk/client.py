"""HTTP client for the versioned Reporting Bureau Police contract."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from telescreen_police_sdk import _json
from telescreen_police_sdk.models import (
    ClaimRequest,
    Manifest,
    Registration,
    ResultEnvelope,
    TaskEnvelope,
)
from telescreen_police_sdk.signing import signature_headers


class BureauError(Exception):
    """Safe Bureau error containing only HTTP status and contract error code."""

    status: int
    code: str

    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(f"Bureau request failed ({status}): {code}")


@dataclass
class BureauClient:
    """Thin asynchronous client with an injected httpx client."""

    base_url: str
    http: httpx.AsyncClient

    async def register(self, manifest: Manifest, enrollment_token: str) -> Registration:
        """Register a Police worker using its enrollment token."""
        response: httpx.Response = await self.http.post(
            self._url("/workers/register"),
            content=_json.dump(manifest.to_json()).encode(),
            headers={
                "Authorization": f"Bearer {enrollment_token}",
                "Content-Type": "application/json",
            },
        )
        self._raise_for_error(response)
        return Registration.from_json(_json.parse(response.content))

    async def claim(
        self, worker_id: str, token: str, request: ClaimRequest
    ) -> tuple[TaskEnvelope, ...]:
        """Claim pull tasks from the Bureau."""
        response: httpx.Response = await self.http.post(
            self._url(f"/workers/{quote(worker_id, safe='')}/claim"),
            content=_json.dump(request.to_json()).encode(),
            headers=self._auth(token),
        )
        self._raise_for_error(response)
        value: _json.Json = _json.parse(response.content)
        tasks_value: _json.Json | None = value.get("tasks")
        tasks: tuple[_json.Json, ...] | None = tasks_value.as_list() if tasks_value else None
        if tasks is None:
            raise ValueError("invalid Bureau response")
        return tuple(TaskEnvelope.from_json(task) for task in tasks)

    async def heartbeat(
        self, worker_id: str, token: str, task_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Extend leases and return task ids cancelled by the Bureau."""
        body: _json.Json = _json.obj(
            (("task_ids", _json.array(tuple(_json.string(item) for item in task_ids))),)
        )
        response: httpx.Response = await self.http.post(
            self._url(f"/workers/{quote(worker_id, safe='')}/heartbeat"),
            content=_json.dump(body).encode(),
            headers=self._auth(token),
        )
        self._raise_for_error(response)
        value: _json.Json = _json.parse(response.content)
        cancelled: _json.Json | None = value.get("cancelled_task_ids")
        items: tuple[_json.Json, ...] | None = cancelled.as_list() if cancelled else None
        if items is None or any(item.as_str() is None for item in items):
            raise ValueError("invalid Bureau response")
        return tuple(item.str_value for item in items)

    async def submit_result(self, token: str, result: ResultEnvelope) -> None:
        """Submit a pull-mode task result."""
        response: httpx.Response = await self.http.post(
            self._url(f"/tasks/{quote(result.task_id, safe='')}/result"),
            content=_json.dump(result.to_json()).encode(),
            headers=self._auth(token),
        )
        self._raise_for_error(response)

    async def callback(self, url: str, result: ResultEnvelope, secret: bytes) -> None:
        """Post an asynchronous result with a fresh signed callback request."""
        body: bytes = _json.dump(result.to_json()).encode()
        headers: tuple[tuple[str, str], ...] = signature_headers(
            secret, body, int(time.time()), secrets.token_hex(16)
        ) + (("Content-Type", "application/json"),)
        response: httpx.Response = await self.http.post(url, content=body, headers=headers)
        self._raise_for_error(response)

    def _url(self, path: str) -> str:
        """Join an API path to the configured versioned base URL."""
        return f"{self.base_url.rstrip('/')}/api/police/v1{path}"

    @staticmethod
    def _auth(token: str) -> tuple[tuple[str, str], ...]:
        """Build a bearer authorization header."""
        return (("Authorization", f"Bearer {token}"), ("Content-Type", "application/json"))

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        """Raise a sanitized error using only the schema's error code."""
        if response.is_success:
            return
        code: str = "request_failed"
        try:
            value: _json.Json = _json.parse(response.content)
            error: _json.Json | None = value.get("error")
            code_value: _json.Json | None = error.get("code") if error else None
            code = code_value.as_str() or code if code_value else code
        except ValueError:
            pass
        raise BureauError(response.status_code, code)
