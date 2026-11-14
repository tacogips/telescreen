"""Validate production Go envelope goldens against the Police contract."""

from pathlib import Path

import pytest

from telescreen.contracts.validator import validate
from telescreen.core.json import JsonKind, JsonValue, dump_json, parse_json
from telescreen.masking.redact import Redactor

GOLDENS: Path = Path(__file__).resolve().parents[2] / "go/testdata/envelopes"
NAMES: tuple[str, ...] = (
    "manifest",
    "claim-request",
    "heartbeat-request",
    "result-detect-findings",
    "result-detect-exposures",
    "result-scan-shard",
    "result-partition",
    "result-partition-root",
    "result-failed-rate-limited",
)
SHARD_SIZE: int = 3


def _required(value: JsonValue, name: str) -> JsonValue:
    member: JsonValue | None = value.get(name)
    assert member is not None, name
    return member


def _clean(value: JsonValue, redactor: Redactor) -> None:
    if value.kind is JsonKind.STRING:
        assert not redactor.has_hits(value.str_value)
    for item in value.items:
        _clean(item, redactor)
    for member in value.members:
        assert not redactor.has_hits(member.name)
        _clean(member.value, redactor)


def test_expected_files() -> None:
    """Require the complete, exact set of goldens."""
    assert {path.name for path in GOLDENS.iterdir()} == {f"{name}.json" for name in NAMES}


@pytest.mark.parametrize("name", NAMES)
def test_envelope_schema_and_redaction(name: str) -> None:
    """Each encoded envelope validates and contains only clean strings."""
    raw: bytes = (GOLDENS / f"{name}.json").read_bytes()
    value: JsonValue = parse_json(raw)
    schema: str = "result-envelope" if name.startswith("result-") else name
    assert validate(f"{schema}.schema.json", raw) == []
    if name.startswith("result-"):
        assert _required(value, "contract_version").as_str() == "1.2"
    if name in ("result-partition", "result-partition-root"):
        assert (
            validate(
                "partition-result.schema.json", dump_json(_required(value, "payload")).encode()
            )
            == []
        )
    _clean(value, Redactor.default())


def test_result_shapes_and_accounting() -> None:
    """Pin versioned members, shard accounting and locator relationships."""
    basic: JsonValue = parse_json((GOLDENS / "result-detect-findings.json").read_bytes())
    exposure: JsonValue = parse_json((GOLDENS / "result-detect-exposures.json").read_bytes())
    shard: JsonValue = parse_json((GOLDENS / "result-scan-shard.json").read_bytes())
    failed: JsonValue = parse_json((GOLDENS / "result-failed-rate-limited.json").read_bytes())
    assert _required(basic, "findings").items and _required(basic, "complete_locators").items
    assert basic.get("resource_exposures") is None and basic.get("resource_inventories") is None
    assert _required(exposure, "resource_exposures").items
    assert exposure.get("resource_inventories") is None
    complete: tuple[JsonValue, ...] = _required(shard, "complete_locators").items
    findings: tuple[JsonValue, ...] = _required(shard, "findings").items
    stats: JsonValue = _required(shard, "stats")
    assert len(complete) + _required(stats, "resources_failed").int_value == SHARD_SIZE
    assert findings
    for result in (basic, exposure, shard):
        result_complete: JsonValue = _required(result, "complete_locators")
        result_findings: JsonValue = _required(result, "findings")
        locators: set[str | None] = {item.as_str() for item in result_complete.items}
        locators.update(_required(item, "locator").as_str() for item in result_findings.items)
        for field in ("resource_exposures", "resource_inventories"):
            entries: JsonValue | None = result.get(field)
            if entries is not None:
                assert entries.items
                assert all(
                    _required(item, "locator").as_str() in locators for item in entries.items
                )
    assert _required(shard, "resource_exposures").items
    inventories: tuple[JsonValue, ...] = _required(shard, "resource_inventories").items
    assert {_required(item, "kind").as_str() for item in inventories} == {"table", "file"}
    assert any(
        item.get("item_count") is not None
        for item in inventories
        if _required(item, "kind").as_str() == "table"
    )
    assert _required(failed, "status").as_str() == "failed"
    error: JsonValue = _required(failed, "error")
    assert _required(error, "code").as_str() == "rate_limited"
    assert _required(error, "retry_after_seconds").as_int() == 7
