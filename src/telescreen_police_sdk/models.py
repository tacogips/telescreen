"""Typed wire models for Police contract version 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from telescreen_police_sdk import _json

CONTRACT_VERSION: str = "1.0"


class Mode(StrEnum):
    """Police transport mode."""

    PULL = "pull"
    PUSH = "push"


class ResultStatus(StrEnum):
    """Task result status."""

    COMPLETED = "completed"
    FAILED = "failed"


class Outcome(StrEnum):
    """Verification outcome."""

    PASSED = "passed"
    FAILED = "failed"


class RuleClass(StrEnum):
    """Supported masked finding categories."""

    CREDENTIAL = "credential"
    TOKEN = "token"
    PRIVATE_KEY = "private_key"
    PASSWORD = "password"
    PAYMENT_CARD = "payment_card"
    NATIONAL_ID = "national_id"
    EMAIL = "email"
    PHONE = "phone"
    ADDRESS = "address"


def _member(value: _json.Json, name: str, required: bool = True) -> _json.Json | None:
    """Read an object member without exposing input values in errors."""
    if value.kind is not _json.JsonKind.OBJECT:
        raise ValueError("invalid contract object")
    result: _json.Json | None = value.get(name)
    if result is None and required:
        raise ValueError("missing contract member")
    return result


def _string(value: _json.Json, name: str, required: bool = True) -> str | None:
    """Read a string member."""
    item: _json.Json | None = _member(value, name, required)
    if item is None:
        return None
    result: str | None = item.as_str()
    if result is None:
        raise ValueError("invalid contract member")
    return result


def _integer(value: _json.Json, name: str) -> int:
    """Read an integer member."""
    item: _json.Json | None = _member(value, name)
    result: int | None = item.as_int() if item is not None else None
    if result is None:
        raise ValueError("invalid contract member")
    return result


def _float(value: _json.Json, name: str) -> float:
    """Read a number member."""
    item: _json.Json | None = _member(value, name)
    result: float | None = item.as_float() if item is not None else None
    if result is None:
        raise ValueError("invalid contract member")
    return result


def _strings(value: _json.Json, name: str) -> tuple[str, ...]:
    """Read an array of strings."""
    item: _json.Json | None = _member(value, name)
    values: tuple[_json.Json, ...] | None = item.as_list() if item is not None else None
    if values is None or any(entry.as_str() is None for entry in values):
        raise ValueError("invalid contract member")
    return tuple(entry.str_value for entry in values)


def _object(pairs: tuple[tuple[str, _json.Json | None], ...]) -> _json.Json:
    """Build an object while omitting absent optional members."""
    return _json.obj(tuple((key, value) for key, value in pairs if value is not None))


def _string_array(values: tuple[str, ...]) -> _json.Json:
    """Build a JSON string array."""
    return _json.array(tuple(_json.string(value) for value in values))


@dataclass(frozen=True)
class Manifest:
    """Police worker capability manifest."""

    name: str
    version: str
    mode: Mode
    task_types: tuple[str, ...]
    labels: tuple[str, ...]
    concurrency: int
    endpoint_url: str | None = None
    contract_version: str = CONTRACT_VERSION

    def to_json(self) -> _json.Json:
        """Serialize the manifest."""
        return _object(
            (
                ("name", _json.string(self.name)),
                ("version", _json.string(self.version)),
                ("contract_version", _json.string(self.contract_version)),
                ("mode", _json.string(self.mode.value)),
                ("task_types", _string_array(self.task_types)),
                ("labels", _string_array(self.labels)),
                ("concurrency", _json.integer(self.concurrency)),
                (
                    "endpoint_url",
                    _json.string(self.endpoint_url) if self.endpoint_url is not None else None,
                ),
            )
        )

    @staticmethod
    def from_json(value: _json.Json) -> Manifest:
        """Parse a manifest."""
        endpoint_url: str | None = _string(value, "endpoint_url", False)
        return Manifest(
            _string(value, "name") or "",
            _string(value, "version") or "",
            Mode(_string(value, "mode") or ""),
            _strings(value, "task_types"),
            _strings(value, "labels"),
            _integer(value, "concurrency"),
            endpoint_url,
            _string(value, "contract_version") or "",
        )


@dataclass(frozen=True)
class Registration:
    """Credentials and lease parameters returned at enrollment."""

    worker_id: str
    lease_seconds: int
    heartbeat_interval_seconds: int
    max_wait_seconds: int
    worker_token: str | None = field(default=None, repr=False)
    signing_secret: str | None = field(default=None, repr=False)

    def to_json(self) -> _json.Json:
        """Serialize a registration response."""
        return _object(
            (
                ("worker_id", _json.string(self.worker_id)),
                ("lease_seconds", _json.integer(self.lease_seconds)),
                ("heartbeat_interval_seconds", _json.integer(self.heartbeat_interval_seconds)),
                ("max_wait_seconds", _json.integer(self.max_wait_seconds)),
                ("worker_token", _json.string(self.worker_token) if self.worker_token else None),
                (
                    "signing_secret",
                    _json.string(self.signing_secret) if self.signing_secret else None,
                ),
            )
        )

    @staticmethod
    def from_json(value: _json.Json) -> Registration:
        """Parse a registration response."""
        return Registration(
            _string(value, "worker_id") or "",
            _integer(value, "lease_seconds"),
            _integer(value, "heartbeat_interval_seconds"),
            _integer(value, "max_wait_seconds"),
            _string(value, "worker_token", False),
            _string(value, "signing_secret", False),
        )


@dataclass(frozen=True)
class ClaimRequest:
    """Pull task claim request."""

    max_tasks: int
    wait_seconds: int
    task_types: tuple[str, ...] = ()

    def to_json(self) -> _json.Json:
        """Serialize a claim request."""
        return _object(
            (
                ("max_tasks", _json.integer(self.max_tasks)),
                ("wait_seconds", _json.integer(self.wait_seconds)),
                ("task_types", _string_array(self.task_types) if self.task_types else None),
            )
        )

    @staticmethod
    def from_json(value: _json.Json) -> ClaimRequest:
        """Parse a claim request."""
        types: tuple[str, ...] = _strings(value, "task_types") if value.get("task_types") else ()
        return ClaimRequest(_integer(value, "max_tasks"), _integer(value, "wait_seconds"), types)


@dataclass(frozen=True)
class Callback:
    """One-time asynchronous result callback details."""

    url: str
    result_token: str = field(repr=False)

    def to_json(self) -> _json.Json:
        """Serialize callback details."""
        return _object(
            (("url", _json.string(self.url)), ("result_token", _json.string(self.result_token)))
        )

    @staticmethod
    def from_json(value: _json.Json) -> Callback:
        """Parse callback details."""
        return Callback(_string(value, "url") or "", _string(value, "result_token") or "")


@dataclass(frozen=True)
class TaskEnvelope:
    """Task sent to a pull or push Police."""

    task_id: str
    task_type: str
    attempt: int
    issued_at: str
    deadline: str
    labels: tuple[str, ...]
    input: _json.Json = field(repr=False)
    callback: Callback | None = None
    contract_version: str = CONTRACT_VERSION

    def to_json(self) -> _json.Json:
        """Serialize a task envelope."""
        return _object(
            (
                ("contract_version", _json.string(self.contract_version)),
                ("task_id", _json.string(self.task_id)),
                ("task_type", _json.string(self.task_type)),
                ("attempt", _json.integer(self.attempt)),
                ("issued_at", _json.string(self.issued_at)),
                ("deadline", _json.string(self.deadline)),
                ("labels", _string_array(self.labels)),
                ("input", self.input),
                ("callback", self.callback.to_json() if self.callback else None),
            )
        )

    @staticmethod
    def from_json(value: _json.Json) -> TaskEnvelope:
        """Parse a task envelope."""
        input_value: _json.Json | None = _member(value, "input")
        if input_value is None:
            raise ValueError("invalid contract member")
        callback_value: _json.Json | None = _member(value, "callback", False)
        return TaskEnvelope(
            _string(value, "task_id") or "",
            _string(value, "task_type") or "",
            _integer(value, "attempt"),
            _string(value, "issued_at") or "",
            _string(value, "deadline") or "",
            _strings(value, "labels"),
            input_value,
            Callback.from_json(callback_value) if callback_value else None,
            _string(value, "contract_version") or "",
        )


@dataclass(frozen=True)
class MaskedFinding:
    """Finding with safe masked display fields and keyed fingerprint."""

    locator: str = field(repr=False)
    rule_id: str
    rule_class: RuleClass
    masked_snippet: str
    location: str
    confidence: float
    external_fingerprint: str

    def to_json(self) -> _json.Json:
        """Serialize a masked finding."""
        return _object(
            (
                ("locator", _json.string(self.locator)),
                ("rule_id", _json.string(self.rule_id)),
                ("rule_class", _json.string(self.rule_class.value)),
                ("masked_snippet", _json.string(self.masked_snippet)),
                ("location", _json.string(self.location)),
                ("confidence", _json.number(self.confidence)),
                ("external_fingerprint", _json.string(self.external_fingerprint)),
            )
        )

    @staticmethod
    def from_json(value: _json.Json) -> MaskedFinding:
        """Parse a masked finding."""
        return MaskedFinding(
            _string(value, "locator") or "",
            _string(value, "rule_id") or "",
            RuleClass(_string(value, "rule_class") or ""),
            _string(value, "masked_snippet") or "",
            _string(value, "location") or "",
            _float(value, "confidence"),
            _string(value, "external_fingerprint") or "",
        )


@dataclass(frozen=True)
class ResultError:
    """Static task failure details."""

    code: str
    detail: str
    retry_after_seconds: float | None = None

    def to_json(self) -> _json.Json:
        """Serialize an error."""
        retry: _json.Json | None = (
            _json.number(self.retry_after_seconds) if self.retry_after_seconds is not None else None
        )
        return _object(
            (
                ("code", _json.string(self.code)),
                ("detail", _json.string(self.detail)),
                ("retry_after_seconds", retry),
            )
        )

    @staticmethod
    def from_json(value: _json.Json) -> ResultError:
        """Parse an error."""
        retry_value: _json.Json | None = _member(value, "retry_after_seconds", False)
        retry: float | None = retry_value.as_float() if retry_value is not None else None
        return ResultError(_string(value, "code") or "", _string(value, "detail") or "", retry)


@dataclass(frozen=True)
class ShardStats:
    """Count-only shard statistics."""

    resources_scanned: int
    resources_failed: int
    bytes_scanned: int

    def to_json(self) -> _json.Json:
        """Serialize shard statistics."""
        return _object(
            (
                ("resources_scanned", _json.integer(self.resources_scanned)),
                ("resources_failed", _json.integer(self.resources_failed)),
                ("bytes_scanned", _json.integer(self.bytes_scanned)),
            )
        )

    @staticmethod
    def from_json(value: _json.Json) -> ShardStats:
        """Parse shard statistics."""
        return ShardStats(
            _integer(value, "resources_scanned"),
            _integer(value, "resources_failed"),
            _integer(value, "bytes_scanned"),
        )


@dataclass(frozen=True)
class ResultEnvelope:
    """Task result, with optional scan and callback fields."""

    task_id: str
    status: ResultStatus
    outcome: Outcome | None = None
    summary: str | None = None
    findings: tuple[MaskedFinding, ...] = ()
    payload: _json.Json | None = field(default=None, repr=False)
    error: ResultError | None = None
    result_token: str | None = field(default=None, repr=False)
    stats: ShardStats | None = None
    complete_locators: tuple[str, ...] = field(default=(), repr=False)
    contract_version: str = CONTRACT_VERSION

    def to_json(self) -> _json.Json:
        """Serialize a result envelope."""
        finding_json: _json.Json | None = (
            _json.array(tuple(item.to_json() for item in self.findings)) if self.findings else None
        )
        locator_json: _json.Json | None = (
            _string_array(self.complete_locators) if self.complete_locators else None
        )
        return _object(
            (
                ("contract_version", _json.string(self.contract_version)),
                ("task_id", _json.string(self.task_id)),
                ("status", _json.string(self.status.value)),
                ("outcome", _json.string(self.outcome.value) if self.outcome else None),
                ("summary", _json.string(self.summary) if self.summary is not None else None),
                ("findings", finding_json),
                ("payload", self.payload),
                ("error", self.error.to_json() if self.error else None),
                ("result_token", _json.string(self.result_token) if self.result_token else None),
                ("stats", self.stats.to_json() if self.stats else None),
                ("complete_locators", locator_json),
            )
        )

    @staticmethod
    def from_json(value: _json.Json) -> ResultEnvelope:
        """Parse a result envelope."""
        findings_json: _json.Json | None = _member(value, "findings", False)
        finding_values: tuple[_json.Json, ...] | None = (
            findings_json.as_list() if findings_json else None
        )
        if finding_values is None:
            finding_values = ()
        error_value: _json.Json | None = _member(value, "error", False)
        stats_value: _json.Json | None = _member(value, "stats", False)
        payload: _json.Json | None = _member(value, "payload", False)
        outcome_text: str | None = _string(value, "outcome", False)
        status_text: str | None = _string(value, "status")
        result_token: str | None = _string(value, "result_token", False)
        summary: str | None = _string(value, "summary", False)
        locators: tuple[str, ...] = (
            _strings(value, "complete_locators") if value.get("complete_locators") else ()
        )
        return ResultEnvelope(
            _string(value, "task_id") or "",
            ResultStatus(status_text or ""),
            Outcome(outcome_text) if outcome_text else None,
            summary,
            tuple(MaskedFinding.from_json(item) for item in finding_values),
            payload,
            ResultError.from_json(error_value) if error_value else None,
            result_token,
            ShardStats.from_json(stats_value) if stats_value else None,
            locators,
            _string(value, "contract_version") or "",
        )


@dataclass(frozen=True)
class Shard:
    """One opaque partition shard."""

    shard_key: str
    descriptor: _json.Json = field(repr=False)
    weight: int

    def to_json(self) -> _json.Json:
        """Serialize a shard."""
        return _object(
            (
                ("shard_key", _json.string(self.shard_key)),
                ("descriptor", self.descriptor),
                ("weight", _json.integer(self.weight)),
            )
        )

    @staticmethod
    def from_json(value: _json.Json) -> Shard:
        """Parse a shard."""
        descriptor: _json.Json | None = _member(value, "descriptor")
        if descriptor is None:
            raise ValueError("invalid contract member")
        return Shard(_string(value, "shard_key") or "", descriptor, _integer(value, "weight"))


@dataclass(frozen=True)
class PartitionResult:
    """Partition page returned by a Police worker."""

    shards: tuple[Shard, ...]
    next_cursor: str | None = None
    sub_corpora: tuple[str, ...] = ()

    def to_json(self) -> _json.Json:
        """Serialize a partition page."""
        shard_values: _json.Json = _json.array(tuple(shard.to_json() for shard in self.shards))
        corpora: _json.Json = _json.array(
            tuple(_object((("corpus_key", _json.string(key)),)) for key in self.sub_corpora)
        )
        cursor: _json.Json = (
            _json.string(self.next_cursor) if self.next_cursor is not None else _json.null()
        )
        return _object(
            (("shards", shard_values), ("next_cursor", cursor), ("sub_corpora", corpora))
        )

    @staticmethod
    def from_json(value: _json.Json) -> PartitionResult:
        """Parse a partition page."""
        shard_json: _json.Json | None = _member(value, "shards")
        shard_values: tuple[_json.Json, ...] | None = shard_json.as_list() if shard_json else None
        corpus_json: _json.Json | None = _member(value, "sub_corpora")
        corpus_values: tuple[_json.Json, ...] | None = (
            corpus_json.as_list() if corpus_json else None
        )
        cursor_value: _json.Json | None = _member(value, "next_cursor")
        if shard_values is None or corpus_values is None or cursor_value is None:
            raise ValueError("invalid contract member")
        cursor: str | None = cursor_value.as_str()
        if cursor_value.kind is not _json.JsonKind.NULL and cursor is None:
            raise ValueError("invalid contract member")
        corpora: tuple[str, ...] = tuple(
            _string(item, "corpus_key") or "" for item in corpus_values
        )
        return PartitionResult(
            tuple(Shard.from_json(item) for item in shard_values), cursor, corpora
        )
