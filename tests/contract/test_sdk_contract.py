"""Validate Python Police SDK payloads against the shared v1 schemas."""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable

from telescreen.contracts.validator import validate
from telescreen_police_sdk import _json
from telescreen_police_sdk.models import (
    Callback,
    ClaimRequest,
    Manifest,
    MaskedFinding,
    Mode,
    Outcome,
    PartitionResult,
    Registration,
    ResultEnvelope,
    ResultError,
    ResultStatus,
    RuleClass,
    Shard,
    ShardStats,
    TaskEnvelope,
)


def test_sdk_models_emit_schema_valid_payloads() -> None:
    """SDK task, registration, result, callback and heartbeat bodies match schemas."""
    manifest: Manifest = Manifest(
        "worker", "1.0", Mode.PULL, ("partition",), ("region=private",), 4
    )
    claim: ClaimRequest = ClaimRequest(4, 10, ("partition",))
    task: TaskEnvelope = TaskEnvelope(
        "task-1",
        "verify_remediation",
        1,
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:05:00Z",
        ("team=security",),
        _json.obj(()),
        Callback("https://bureau.example.invalid/callback", "result-token"),
    )
    registration: Registration = Registration("worker-1", 60, 20, 30, "worker-token")
    finding: MaskedFinding = MaskedFinding(
        "resource-1", "email", RuleClass.EMAIL, "s***[20]", "body", 0.9, "f" * 64
    )
    failed: ResultEnvelope = ResultEnvelope(
        "task-failed",
        ResultStatus.FAILED,
        error=ResultError("rate_limited", "Retry after the indicated delay.", 5.0),
    )
    scan_shard: ResultEnvelope = ResultEnvelope(
        "task-scan",
        ResultStatus.COMPLETED,
        findings=(finding,),
        stats=ShardStats(2, 0, 64),
        complete_locators=("resource-1",),
    )
    callback_result: ResultEnvelope = ResultEnvelope(
        "task-callback",
        ResultStatus.COMPLETED,
        outcome=Outcome.PASSED,
        result_token="result-token",
    )
    heartbeat: _json.Json = _json.obj((("task_ids", _json.array((_json.string("task-1"),))),))
    error_response: _json.Json = _json.obj(
        (("error", ResultError("validation_error", "The request is invalid.").to_json()),)
    )
    partition: PartitionResult = PartitionResult(
        (Shard("shard-1", _json.obj((("table_id", _json.string("table-1")),)), 1),)
    )
    fixtures: tuple[tuple[str, _json.Json], ...] = (
        ("manifest.schema.json", manifest.to_json()),
        ("claim-request.schema.json", claim.to_json()),
        ("task-envelope.schema.json", task.to_json()),
        ("registration.schema.json", registration.to_json()),
        ("masked-finding.schema.json", finding.to_json()),
        ("partition-result.schema.json", partition.to_json()),
        ("result-envelope.schema.json", failed.to_json()),
        ("result-envelope.schema.json", scan_shard.to_json()),
        ("result-envelope.schema.json", callback_result.to_json()),
        ("heartbeat-request.schema.json", heartbeat),
        ("error.schema.json", error_response),
    )
    for schema, value in fixtures:
        assert validate(schema, _json.dump(value).encode()) == []


def test_sdk_parses_every_shared_contract_example() -> None:
    """Every contract example round-trips through its SDK model or closed JSON model."""
    examples: Traversable = files("telescreen.contracts").joinpath("police", "v1", "examples")
    names: tuple[str, ...] = tuple(sorted(item.name for item in examples.iterdir()))
    assert len(names) == 14
    for name in names:
        value: _json.Json = _json.parse(examples.joinpath(name).read_bytes())
        round_trip: _json.Json = value
        if name.startswith("manifest-"):
            round_trip = Manifest.from_json(value).to_json()
        elif name == "claim-request.json":
            round_trip = ClaimRequest.from_json(value).to_json()
        elif name == "claim-response.json":
            tasks_value: _json.Json | None = value.get("tasks")
            tasks: tuple[_json.Json, ...] | None = tasks_value.as_list() if tasks_value else None
            assert tasks is not None
            encoded_tasks: _json.Json = _json.array(
                tuple(TaskEnvelope.from_json(task).to_json() for task in tasks)
            )
            round_trip = _json.obj((("tasks", encoded_tasks),))
        elif name == "masked-finding.json":
            round_trip = MaskedFinding.from_json(value).to_json()
        elif name == "partition-result.json":
            round_trip = PartitionResult.from_json(value).to_json()
        elif name == "registration.json":
            round_trip = Registration.from_json(value).to_json()
        elif name.startswith("result-envelope"):
            round_trip = ResultEnvelope.from_json(value).to_json()
        elif name == "task-envelope.json":
            round_trip = TaskEnvelope.from_json(value).to_json()
        assert json.loads(_json.dump(round_trip)) == json.loads(_json.dump(value)), name
