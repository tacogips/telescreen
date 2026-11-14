"""Durable engine handler for signed Police webhook delivery."""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, dump_json, json_int, json_object, json_str, parse_json
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import EngineTaskKind, ErrorCode, PoliceTaskStatus
from telescreen.db.models import PoliceTaskRow, PoliceWorkerRow
from telescreen.police.registration import hash_token
from telescreen.police.results import ResultService
from telescreen.police.signing import derive_push_secret, sign
from telescreen.ports.sweeps import ShardFailure
from telescreen.ports.workflow import ClaimedTask, TaskHandler, TaskResult


def _input_value(task: PoliceTaskRow) -> JsonValue:
    """Return the already persisted closed task input."""
    return parse_json(task.input_json)


@dataclass(frozen=True)
class PolicePushDeliveryHandler(TaskHandler):
    """Send signed task envelopes without holding a database transaction."""

    sessions: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    signing_key: SecretValue
    public_base_url: str
    results: ResultService
    clock: Clock
    kind: EngineTaskKind = EngineTaskKind.POLICE_PUSH_DELIVERY

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Deliver once and return the engine retry disposition when needed."""
        token: str = secrets.token_urlsafe(32)
        async with self.sessions.begin() as session:
            police_task: PoliceTaskRow | None = await session.get(PoliceTaskRow, task.ref_id)
            if police_task is None or police_task.status in {
                PoliceTaskStatus.COMPLETED.value,
                PoliceTaskStatus.FAILED.value,
                PoliceTaskStatus.EXPIRED.value,
                PoliceTaskStatus.CANCELLED.value,
            }:
                return TaskResult.done()
            worker: PoliceWorkerRow | None = (
                await session.get(PoliceWorkerRow, police_task.worker_id)
                if police_task.worker_id is not None
                else None
            )
            if worker is None or worker.endpoint_url is None:
                police_task.status = PoliceTaskStatus.FAILED.value
                police_task.completed_at = ensure_utc(self.clock.now())
                await self._fail_map_task(session, police_task)
                return TaskResult.dead(ErrorCode.UNKNOWN_SOURCE)
            police_task.attempt += 1
            police_task.callback_token_hash = hash_token(token)
            police_task.callback_token_used_at = None
            now: datetime = ensure_utc(self.clock.now())
            secret: str = derive_push_secret(self.signing_key.value.encode("utf-8"), worker.id)
            envelope: JsonValue = json_object(
                (
                    ("contract_version", json_str(worker.contract_version)),
                    ("task_id", json_str(police_task.id)),
                    ("task_type", json_str(police_task.task_type)),
                    ("attempt", json_int(police_task.attempt)),
                    ("issued_at", json_str(now.isoformat())),
                    ("deadline", json_str(ensure_utc(police_task.deadline).isoformat())),
                    (
                        "labels",
                        parse_json(
                            "["
                            + ",".join(
                                '"' + label + '"'
                                for label in filter(None, police_task.labels.split(","))
                            )
                            + "]"
                        ),
                    ),
                    ("input", _input_value(police_task)),
                    (
                        "callback",
                        json_object(
                            (
                                (
                                    "url",
                                    json_str(
                                        f"{self.public_base_url.rstrip('/')}/api/police/v1/callbacks/{police_task.id}"
                                    ),
                                ),
                                ("result_token", json_str(token)),
                            )
                        ),
                    ),
                )
            )
            body: bytes = dump_json(envelope).encode("utf-8")
            timestamp: str = str(int(now.timestamp()))
            nonce: str = secrets.token_hex(16)
            signature: str = sign(secret, body, timestamp, nonce)
            headers: tuple[tuple[str, str], ...] = (
                ("Content-Type", "application/json"),
                ("X-Telescreen-Timestamp", timestamp),
                ("X-Telescreen-Nonce", nonce),
                ("X-Telescreen-Signature", signature),
            )
            endpoint: str = worker.endpoint_url
            task_id: str = police_task.id
        try:
            response: httpx.Response = await self.http.post(endpoint, content=body, headers=headers)
        except httpx.HTTPError:
            response = httpx.Response(599)
        if response.status_code == 200:
            try:
                async with self.sessions.begin() as session:
                    police_task = await session.get(PoliceTaskRow, task_id)
                    worker = (
                        await session.get(PoliceWorkerRow, police_task.worker_id)
                        if police_task is not None and police_task.worker_id is not None
                        else None
                    )
                    if police_task is None:
                        return TaskResult.done()
                    await self.results.accept(session, police_task, response.content, worker)
                return TaskResult.done()
            except TelescreenError:
                return await self._retry_or_fail(task_id, ErrorCode.DELIVERY_FAILED)
        if response.status_code == 202:
            async with self.sessions.begin() as session:
                police_task = await session.get(PoliceTaskRow, task_id)
                if police_task is not None and police_task.status == PoliceTaskStatus.QUEUED.value:
                    police_task.status = PoliceTaskStatus.DELIVERED.value
                    police_task.lease_expires_at = None
                worker = (
                    await session.get(PoliceWorkerRow, police_task.worker_id)
                    if police_task is not None and police_task.worker_id is not None
                    else None
                )
                if worker is not None:
                    worker.last_delivery_at = self.clock.now()
            return TaskResult.done()
        return await self._retry_or_fail(task_id, ErrorCode.DELIVERY_FAILED)

    async def _retry_or_fail(self, task_id: str, error_code: ErrorCode) -> TaskResult:
        """Use bounded exponential backoff and fail after the request limit."""
        async with self.sessions.begin() as session:
            task: PoliceTaskRow | None = await session.get(PoliceTaskRow, task_id)
            if task is None or task.status in {
                PoliceTaskStatus.COMPLETED.value,
                PoliceTaskStatus.CANCELLED.value,
            }:
                return TaskResult.done()
            now: datetime = ensure_utc(self.clock.now())
            delay_seconds: int = 10 * (2 ** max(task.attempt - 1, 0))
            retry_at: datetime = now + timedelta(seconds=delay_seconds)
            if task.attempt < task.max_attempts and retry_at < ensure_utc(task.deadline):
                return TaskResult.retry(retry_at, error_code)
            task.status = PoliceTaskStatus.FAILED.value
            task.completed_at = now
            await self._fail_map_task(session, task)
            return TaskResult.dead(error_code)

    async def _fail_map_task(self, session: AsyncSession, task: PoliceTaskRow) -> None:
        """Notify map-reduce when a push task cannot be delivered further."""
        if task.task_type in {"partition", "scan_shard"}:
            await self.results.map_sink.task_failed(
                session,
                task.id,
                ShardFailure(ErrorCode.FETCH_FAILED, None),
            )
