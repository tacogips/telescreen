"""Police worker enrollment and one-time credential issuance."""

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import ParseResult, urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.contracts.validator import SchemaError, validate
from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_int,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import ActorKind, ErrorCode, PoliceMode, PoliceWorkerStatus
from telescreen.db.models import PoliceWorkerRow
from telescreen.police.repository import PoliceRepository
from telescreen.police.signing import derive_push_secret
from telescreen.ports.audit import AuditEntry, AuditLog


def hash_token(token: str) -> str:
    """Hash a bearer token for indexed storage and constant-time comparison."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _member(value: JsonValue, name: str) -> JsonValue | None:
    """Get one object member, returning none for non-object values."""
    return value.get(name) if value.kind is JsonKind.OBJECT else None


def _string(value: JsonValue | None) -> str:
    """Read a required string after contract validation."""
    if value is None or value.kind is not JsonKind.STRING:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid police registration")
    return value.str_value


def _strings(value: JsonValue | None) -> tuple[str, ...]:
    """Read an array of strings after contract validation."""
    if value is None or value.kind is not JsonKind.ARRAY:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid police registration")
    result: list[str] = []
    for item in value.items:
        if item.kind is not JsonKind.STRING:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid police registration")
        result.append(item.str_value)
    return tuple(result)


@dataclass(frozen=True)
class RegistrationService:
    """Validate registration manifests and issue credentials once."""

    enrollment_token: SecretValue
    signing_key: SecretValue | None
    allow_http: bool
    audit: AuditLog
    clock: Clock

    def check_enrollment_token(self, token: str) -> bool:
        """Compare an enrollment bearer token without exposing either value."""
        return hmac.compare_digest(hash_token(token), hash_token(self.enrollment_token.value))

    async def register(self, session: AsyncSession, raw_manifest: bytes) -> bytes:
        """Persist a valid worker manifest and return its credential once."""
        manifest: JsonValue = parse_json(raw_manifest)
        contract_version: str = _string(_member(manifest, "contract_version"))
        if not contract_version.startswith("1."):
            raise TelescreenError(
                ErrorCode.UNSUPPORTED_CONTRACT_VERSION, "unsupported police contract version"
            )
        errors: list[SchemaError] = validate("manifest.schema.json", raw_manifest)
        if errors:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid police registration")
        mode: PoliceMode = PoliceMode(_string(_member(manifest, "mode")))
        endpoint: str | None = None
        endpoint_value: JsonValue | None = _member(manifest, "endpoint_url")
        if endpoint_value is not None:
            endpoint = _string(endpoint_value)
        if mode is PoliceMode.PUSH:
            signing_key: SecretValue | None = self.signing_key
            if signing_key is None:
                raise TelescreenError(ErrorCode.CONFIG_ERROR, "police signing key is unavailable")
            parsed: ParseResult = urlparse(endpoint or "")
            if parsed.scheme != "https" and not (self.allow_http and parsed.scheme == "http"):
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "push endpoint must use HTTPS")
            if not parsed.netloc:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid push endpoint")

        worker_id: str = new_id()
        name: str = _string(_member(manifest, "name"))
        worker_token: str | None = None
        signing_secret: str | None = None
        if mode is PoliceMode.PULL:
            worker_token = secrets.token_urlsafe(32)
        else:
            signing_secret = derive_push_secret(signing_key.value.encode("utf-8"), worker_id)
        task_types: tuple[str, ...] = _strings(_member(manifest, "task_types"))
        labels: tuple[str, ...] = _strings(_member(manifest, "labels"))
        concurrency_value: JsonValue | None = _member(manifest, "concurrency")
        if concurrency_value is None or concurrency_value.kind is not JsonKind.INT:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid police registration")
        now: datetime = self.clock.now()
        session.add(
            PoliceWorkerRow(
                id=worker_id,
                name=name,
                version=_string(_member(manifest, "version")),
                mode=mode.value,
                contract_version=contract_version,
                task_types=",".join(task_types),
                labels=",".join(labels),
                concurrency=concurrency_value.int_value,
                endpoint_url=endpoint,
                token_hash=hash_token(worker_token) if worker_token is not None else None,
                status=PoliceWorkerStatus.ACTIVE.value,
                created_at=now,
            )
        )
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:police-registration",
                "police.registered",
                "police_worker",
                worker_id,
                "Police worker registered",
                parse_json(json.dumps({"worker_id": worker_id, "mode": mode.value})),
            ),
        )
        response_members: list[tuple[str, JsonValue]] = [
            ("worker_id", json_str(worker_id)),
            ("lease_seconds", json_int(60)),
            ("heartbeat_interval_seconds", json_int(20)),
            ("max_wait_seconds", json_int(30)),
        ]
        if worker_token is not None:
            response_members.append(("worker_token", json_str(worker_token)))
        if signing_secret is not None:
            response_members.append(("signing_secret", json_str(signing_secret)))
        response: bytes = dump_json(json_object(tuple(response_members))).encode("utf-8")
        if validate("registration.schema.json", response):
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "invalid police registration response"
            )
        return response


async def worker_by_token(
    session: AsyncSession, repository: PoliceRepository, worker_id: str, token: str
) -> PoliceWorkerRow:
    """Authenticate an active worker and its path identity."""
    worker: PoliceWorkerRow | None = await repository.worker(session, worker_id)
    supplied_hash: str = hash_token(token)
    if (
        worker is None
        or worker.status != PoliceWorkerStatus.ACTIVE.value
        or worker.token_hash is None
        or not hmac.compare_digest(worker.token_hash, supplied_hash)
    ):
        raise TelescreenError(ErrorCode.UNAUTHORIZED, "authentication required")
    return worker
