"""Police v1 contract endpoints and worker administration routes."""

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import ApiContext, read_limited_body
from telescreen.api.common.auth import Principal, declare_external, require_permission
from telescreen.contracts.validator import openapi_document, validate
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_array,
    json_int,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.permissions import Permission
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import (
    ErrorCode,
    ExternalAuthKind,
    PoliceTaskStatus,
    PoliceWorkerStatus,
)
from telescreen.db.models import PoliceTaskRow, PoliceWorkerRow
from telescreen.db.replay import ReplayNonceStore
from telescreen.police.claims import ClaimRequest, ClaimService
from telescreen.police.health import WorkerHealth, list_worker_health
from telescreen.police.registration import RegistrationService, hash_token, worker_by_token
from telescreen.police.repository import PoliceRepository
from telescreen.police.results import ResultService
from telescreen.police.signing import (
    SignatureHeaders,
    derive_push_secret,
    replay_expiry,
    verify_signature,
)
from telescreen.ports.workflow import TaskQueue


@dataclass(frozen=True)
class PoliceRouterDeps:
    """Dependencies needed by the Police contract and admin routes."""

    ctx: ApiContext
    registration: RegistrationService
    claims: ClaimService
    results: ResultService
    replay: ReplayNonceStore
    signing_key: SecretValue | None
    repository: PoliceRepository = PoliceRepository()
    queue: TaskQueue | None = None


def _object(value: JsonValue) -> bool:
    """Return whether a parsed JSON value is an object."""
    return value.kind is JsonKind.OBJECT


def _field(value: JsonValue, name: str) -> JsonValue | None:
    """Return one object member."""
    return value.get(name) if _object(value) else None


def _string(value: JsonValue | None, default: str = "") -> str:
    """Read a string member after schema validation."""
    return value.str_value if value is not None and value.kind is JsonKind.STRING else default


def _integer(value: JsonValue | None, default: int) -> int:
    """Read an integer member after schema validation."""
    return value.int_value if value is not None and value.kind is JsonKind.INT else default


def _array_strings(value: JsonValue | None) -> tuple[str, ...]:
    """Read string items from a schema-validated array."""
    if value is None or value.kind is not JsonKind.ARRAY:
        return ()
    return tuple(item.str_value for item in value.items if item.kind is JsonKind.STRING)


def _schema_check(name: str, body: bytes) -> None:
    """Reject invalid contract bodies without exposing instance values."""
    if validate(name, body):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "request does not match Police contract")


def _bearer(request: Request) -> str:
    """Extract a nonempty bearer credential or raise a static auth error."""
    authorization: str | None = request.headers.get("authorization")
    if authorization is None or not authorization.startswith("Bearer "):
        raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
    token: str = authorization[7:]
    if not token:
        raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
    return token


def build_police_router(deps: PoliceRouterDeps) -> APIRouter:
    """Build the versioned Police endpoints and worker administration routes."""
    router: APIRouter = APIRouter(tags=["police"])

    @router.get(
        "/api/police/v1/openapi.json",
        dependencies=[Depends(declare_external(ExternalAuthKind.POLICE_CONTRACT))],
    )
    async def contract_openapi() -> JSONResponse:
        """Return the versioned Police OpenAPI document."""
        return JSONResponse(content=json.loads(openapi_document()))

    @router.post(
        "/api/police/v1/workers/register",
        dependencies=[Depends(declare_external(ExternalAuthKind.POLICE_CONTRACT))],
    )
    async def register(request: Request) -> JSONResponse:
        """Enroll a worker and return its credential exactly once."""
        token: str = _bearer(request)
        if not deps.registration.check_enrollment_token(token):
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        body: bytes = await read_limited_body(request, 1_048_576)
        manifest: JsonValue = parse_json(body)
        contract_version: str = _string(_field(manifest, "contract_version"))
        if not contract_version.startswith("1."):
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": ErrorCode.UNSUPPORTED_CONTRACT_VERSION.value,
                        "detail": "unsupported police contract version",
                    }
                },
            )
        async with deps.ctx.sessions.begin() as session:
            response: bytes = await deps.registration.register(session, body)
        return JSONResponse(status_code=201, content=json.loads(response))

    @router.post(
        "/api/police/v1/workers/{worker_id}/claim",
        dependencies=[Depends(declare_external(ExternalAuthKind.POLICE_CONTRACT))],
    )
    async def claim(worker_id: str, request: Request) -> JSONResponse:
        """Long-poll and lease compatible work for a pull worker."""
        body: bytes = await read_limited_body(request, 1_048_576)
        _schema_check("claim-request.schema.json", body)
        parsed: JsonValue = parse_json(body)
        ask: ClaimRequest = ClaimRequest(
            _integer(_field(parsed, "max_tasks"), 1),
            _integer(_field(parsed, "wait_seconds"), 0),
            _array_strings(_field(parsed, "task_types")),
        )
        token: str = _bearer(request)
        async with deps.ctx.sessions() as session:
            worker: PoliceWorkerRow = await worker_by_token(
                session, deps.repository, worker_id, token
            )
        tasks: tuple[PoliceTaskRow, ...] = await deps.claims.claim(deps.ctx.sessions, worker, ask)
        rendered: list[JsonValue] = []
        for task in tasks:
            rendered.append(
                json_object(
                    (
                        ("contract_version", json_str(worker.contract_version)),
                        ("task_id", json_str(task.id)),
                        ("task_type", json_str(task.task_type)),
                        ("attempt", json_int(task.attempt)),
                        ("issued_at", json_str(ensure_utc(task.created_at).isoformat())),
                        ("deadline", json_str(ensure_utc(task.deadline).isoformat())),
                        (
                            "labels",
                            json_array(
                                tuple(
                                    json_str(item) for item in filter(None, task.labels.split(","))
                                )
                            ),
                        ),
                        ("input", parse_json(task.input_json)),
                    )
                )
            )
        response_value: JsonValue = json_object((("tasks", json_array(tuple(rendered))),))
        response_bytes: bytes = dump_json(response_value).encode("utf-8")
        _schema_check("claim-response.schema.json", response_bytes)
        return JSONResponse(content=json.loads(response_bytes))

    @router.post(
        "/api/police/v1/workers/{worker_id}/heartbeat",
        dependencies=[Depends(declare_external(ExternalAuthKind.POLICE_CONTRACT))],
    )
    async def heartbeat(worker_id: str, request: Request) -> JSONResponse:
        """Extend task leases held by an authenticated worker."""
        body: bytes = await read_limited_body(request, 1_048_576)
        _schema_check("heartbeat-request.schema.json", body)
        parsed: JsonValue = parse_json(body)
        task_ids: tuple[str, ...] = _array_strings(_field(parsed, "task_ids"))
        token: str = _bearer(request)
        async with deps.ctx.sessions.begin() as session:
            worker: PoliceWorkerRow = await worker_by_token(
                session, deps.repository, worker_id, token
            )
            cancelled: tuple[str, ...] = await deps.claims.heartbeat(session, worker, task_ids)
        response_value: JsonValue = json_object(
            (("cancelled_task_ids", json_array(tuple(json_str(item) for item in cancelled))),)
        )
        response_bytes: bytes = dump_json(response_value).encode("utf-8")
        _schema_check("heartbeat-response.schema.json", response_bytes)
        return JSONResponse(content=json.loads(response_bytes))

    @router.post(
        "/api/police/v1/tasks/{task_id}/result",
        dependencies=[Depends(declare_external(ExternalAuthKind.POLICE_CONTRACT))],
    )
    async def submit_result(task_id: str, request: Request) -> JSONResponse:
        """Accept a result from a currently leased pull task."""
        body: bytes = await read_limited_body(request, 1_048_576)
        _schema_check("result-envelope.schema.json", body)
        token: str = _bearer(request)
        rejection: TelescreenError | None = None
        async with deps.ctx.sessions.begin() as session:
            worker: PoliceWorkerRow = await _worker_for_token(session, deps, token)
            task: PoliceTaskRow | None = await session.get(PoliceTaskRow, task_id)
            if (
                task is None
                or task.worker_id != worker.id
                or task.status != PoliceTaskStatus.LEASED.value
            ):
                raise TelescreenError(ErrorCode.CONFLICT, "task is not leased to this worker")
            try:
                await deps.results.accept(session, task, body, worker)
            except TelescreenError as error:
                if error.code is not ErrorCode.RAW_VALUE_DETECTED:
                    raise
                rejection = error
        if rejection is not None:
            return JSONResponse(
                status_code=422,
                content={"error": {"code": rejection.code.value, "detail": rejection.detail}},
            )
        return JSONResponse(status_code=204, content=None)

    @router.post(
        "/api/police/v1/callbacks/{task_id}",
        dependencies=[Depends(declare_external(ExternalAuthKind.POLICE_CONTRACT))],
    )
    async def callback(task_id: str, request: Request) -> JSONResponse:
        """Verify a signed asynchronous push result and consume its token once."""
        body: bytes = await read_limited_body(request, 1_048_576)
        _schema_check("result-envelope.schema.json", body)
        if deps.signing_key is None:
            raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
        headers: SignatureHeaders = SignatureHeaders(
            request.headers.get("x-telescreen-timestamp", ""),
            request.headers.get("x-telescreen-nonce", ""),
            request.headers.get("x-telescreen-signature", ""),
        )
        parsed: JsonValue = parse_json(body)
        result_token: str = _string(_field(parsed, "result_token"))
        rejection: TelescreenError | None = None
        async with deps.ctx.sessions.begin() as session:
            task: PoliceTaskRow | None = await session.get(PoliceTaskRow, task_id)
            worker: PoliceWorkerRow | None = (
                await session.get(PoliceWorkerRow, task.worker_id)
                if task is not None and task.worker_id is not None
                else None
            )
            if task is None or worker is None or task.callback_token_hash is None:
                raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
            if worker.mode != "push":
                raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
            if task.callback_token_used_at is not None:
                raise TelescreenError(ErrorCode.CONFLICT, "callback token already used")
            if ensure_utc(task.deadline) <= ensure_utc(deps.ctx.clock.now()):
                raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
            supplied_hash: str = hashlib.sha256(result_token.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(task.callback_token_hash, supplied_hash):
                raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
            secret: str = derive_push_secret(deps.signing_key.value.encode("utf-8"), worker.id)
            if not verify_signature(headers, body, secret, deps.ctx.clock.now(), True):
                raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
            accepted: bool = await deps.replay.remember(
                session,
                "police-callback:" + worker.id,
                headers.nonce,
                replay_expiry(deps.ctx.clock.now()),
                deps.ctx.clock.now(),
            )
            if not accepted:
                raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
            try:
                await deps.results.accept(session, task, body, worker)
            except TelescreenError as error:
                if error.code is not ErrorCode.RAW_VALUE_DETECTED:
                    raise
                rejection = error
            else:
                task.callback_token_used_at = deps.ctx.clock.now()
        if rejection is not None:
            return JSONResponse(
                status_code=422,
                content={"error": {"code": rejection.code.value, "detail": rejection.detail}},
            )
        return JSONResponse(status_code=204, content=None)

    @router.get("/api/police/workers")
    async def list_workers(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.POLICE_READ))
        ],
    ) -> JSONResponse:
        """List safe worker capability and liveness metadata."""
        del principal
        async with deps.ctx.sessions() as session:
            workers: tuple[PoliceWorkerRow, ...] = await deps.repository.workers(session)
            health: tuple[tuple[str, WorkerHealth], ...] = await list_worker_health(
                session, deps.ctx.clock.now()
            )
        return JSONResponse(
            content={
                "items": [
                    {
                        "id": worker.id,
                        "name": worker.name,
                        "version": worker.version,
                        "mode": worker.mode,
                        "task_types": list(filter(None, worker.task_types.split(","))),
                        "labels": list(filter(None, worker.labels.split(","))),
                        "concurrency": worker.concurrency,
                        "status": worker.status,
                        "last_heartbeat_at": worker.last_heartbeat_at.isoformat()
                        if worker.last_heartbeat_at
                        else None,
                        "health": {
                            "status": worker_health.status.value,
                            "conditions": [
                                condition.value for condition in worker_health.conditions
                            ],
                        }
                        if (worker_health := _health_for(health, worker.id)) is not None
                        else None,
                    }
                    for worker in workers
                ],
                "next_cursor": None,
            }
        )

    @router.post("/api/police/workers/{worker_id}/disable")
    async def disable_worker(
        worker_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.POLICE_WRITE))
        ],
    ) -> JSONResponse:
        """Disable a worker and cancel its ready push delivery when possible."""
        del principal
        async with deps.ctx.sessions.begin() as session:
            worker: PoliceWorkerRow | None = await session.get(PoliceWorkerRow, worker_id)
            if worker is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "police worker not found")
            worker.status = PoliceWorkerStatus.DISABLED.value
            if deps.queue is not None:
                task_ids: tuple[str, ...] = tuple(
                    await session.scalars(
                        select(PoliceTaskRow.id).where(
                            PoliceTaskRow.worker_id == worker_id,
                            PoliceTaskRow.status.in_(
                                (PoliceTaskStatus.QUEUED.value, PoliceTaskStatus.DELIVERED.value)
                            ),
                        )
                    )
                )
                for task_id in task_ids:
                    await deps.queue.cancel_by_ref(session, task_id)
        return JSONResponse(content={"id": worker_id, "status": PoliceWorkerStatus.DISABLED.value})

    return router


def _health_for(
    health: tuple[tuple[str, WorkerHealth], ...], worker_id: str
) -> WorkerHealth | None:
    """Find the calculated health view for a worker."""
    return next((view for candidate, view in health if candidate == worker_id), None)


async def _worker_for_token(
    session: AsyncSession, deps: PoliceRouterDeps, token: str
) -> PoliceWorkerRow:
    """Resolve a worker using the unique stored token hash."""
    supplied_hash: str = hash_token(token)
    worker: PoliceWorkerRow | None = await session.scalar(
        select(PoliceWorkerRow).where(PoliceWorkerRow.token_hash == supplied_hash)
    )
    if (
        worker is not None
        and worker.token_hash is not None
        and hmac.compare_digest(worker.token_hash, supplied_hash)
        and worker.status == PoliceWorkerStatus.ACTIVE.value
    ):
        return worker
    raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
