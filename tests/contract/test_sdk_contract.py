"""Validate Python Police SDK payloads against the shared v1 schemas."""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable

from telescreen.contracts.validator import validate
from telescreen_police_sdk import _json
from telescreen_police_sdk.models import (
    ClaimRequest,
    Manifest,
    Mode,
    PartitionResult,
    ResultEnvelope,
    ResultStatus,
    Shard,
)


def test_sdk_models_emit_schema_valid_payloads() -> None:
    """Each supported outbound model matches its corresponding schema."""
    manifest: Manifest = Manifest(
        "worker", "1.0", Mode.PULL, ("partition",), ("region=private",), 4
    )
    claim: ClaimRequest = ClaimRequest(4, 10, ("partition",))
    result: ResultEnvelope = ResultEnvelope(
        "task-1",
        ResultStatus.COMPLETED,
        payload=PartitionResult(
            (Shard("shard-1", _json.obj((("table_id", _json.string("table-1")),)), 1),)
        ).to_json(),
    )
    fixtures: tuple[tuple[str, _json.Json], ...] = (
        ("manifest.schema.json", manifest.to_json()),
        ("claim-request.schema.json", claim.to_json()),
        ("result-envelope.schema.json", result.to_json()),
    )
    for schema, value in fixtures:
        assert validate(schema, _json.dump(value).encode()) == []


def test_sdk_parses_every_shared_contract_example() -> None:
    """Models parse every example shape published in the contract package."""
    examples: Traversable = files("telescreen.contracts").joinpath("police", "v1", "examples")
    for name in (
        "task-envelope.json",
        "result-envelope.json",
        "result-envelope-failed.json",
        "result-envelope-scan-shard.json",
        "partition-result.json",
    ):
        value: _json.Json = _json.parse(examples.joinpath(name).read_bytes())
        if name == "task-envelope.json":
            from telescreen_police_sdk.models import TaskEnvelope

            TaskEnvelope.from_json(value)
        elif name == "partition-result.json":
            from telescreen_police_sdk.models import PartitionResult

            PartitionResult.from_json(value)
        else:
            ResultEnvelope.from_json(value)
