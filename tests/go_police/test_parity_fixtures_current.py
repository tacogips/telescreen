"""Verify the Python reference inputs used by Go parity fixtures."""

import base64
import sys
import unicodedata
from pathlib import Path

from gen_go_police_fixtures import GeneratedFile, render_outputs
from go_police_fixture_cases import (
    detector_cases,
    exposure_cases,
    fingerprint_cases,
    generated_detector_cases,
    line_segment_cases,
    matching_cases,
    redactor_cases,
    sealing_cases,
)

from telescreen.core.json import JsonValue, parse_json
from telescreen.detection.rules.catalog import RULES


def _decode_fixture(value: str, marker: str) -> str:
    if marker != "xor-v1:telescreen-go-parity-fixture":
        raise ValueError("unsupported fixture obfuscation")
    key: bytes = b"telescreen-go-parity-fixture"
    data: bytes = base64.b64decode(value, validate=True)
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data)).decode()


def test_fixture_obfuscation() -> None:
    """The fixture loader checks the marker and reverses the encoding."""
    import pytest

    fixture: JsonValue = fingerprint_cases(b"go-parity-fixture-key-0000000000000000")
    encoded: JsonValue | None = fixture.get("key_b64")
    assert encoded is not None
    assert _decode_fixture(encoded.str_value, "xor-v1:telescreen-go-parity-fixture") == (
        "go-parity-fixture-key-0000000000000000"
    )
    with pytest.raises(ValueError, match="unsupported fixture obfuscation"):
        _decode_fixture(encoded.str_value, "unknown")


def test_generated_detector_inputs_are_deterministic() -> None:
    """The seeded corpus is stable and contains the planned number of inputs."""
    cases: tuple[str, ...] = generated_detector_cases()
    assert len(cases) == 2000
    assert cases == generated_detector_cases()
    assert len(detector_cases().items) > len(cases)


def test_derived_fingerprint_inputs_exist() -> None:
    """The fixture builder includes multiple redactor-triggered base digests."""
    body: JsonValue = fingerprint_cases(b"go-parity-fixture-key-0000000000000000")
    findings: JsonValue | None = body.get("finding")
    assert findings is not None
    derived: list[JsonValue] = [
        count for case in findings.items if (count := case.get("derived_n")) is not None
    ]
    assert sum(count.int_value > 0 for count in derived) >= 2


def test_exposure_and_matching_case_coverage() -> None:
    """Reference classifiers generate both positive and negative outcomes."""
    exposures: JsonValue = exposure_cases()
    matches: JsonValue = matching_cases()
    assert len(exposures.items) >= 12
    assert len(matches.items) >= 100
    decisions: tuple[JsonValue | None, ...] = tuple(case.get("matches") for case in matches.items)
    assert any(value is not None and value.bool_value for value in decisions)
    assert any(value is not None and not value.bool_value for value in decisions)


def test_segment_redaction_and_sealing_builders() -> None:
    """The reference builders exercise splitlines and masked findings."""
    lines: JsonValue = line_segment_cases()
    redactions: JsonValue = redactor_cases()
    sealed: JsonValue = sealing_cases(b"go-parity-fixture-key-0000000000000000")
    assert len(lines.items) >= 14
    assert any(
        (hit := case.get("has_hits")) is not None and hit.bool_value for case in redactions.items
    )
    assert any(
        (findings := case.get("findings")) is not None and findings.items for case in sealed.items
    )
    snippets: list[str] = [
        _decode_fixture(value.str_value, "xor-v1:telescreen-go-parity-fixture")
        for case in sealed.items
        if (findings := case.get("findings")) is not None
        for finding in findings.items
        if (value := finding.get("masked_snippet_b64")) is not None
    ]
    assert "[REDACTED]" in snippets


def test_generated_outputs_are_current() -> None:
    """Every generated table and fixture matches the committed bytes."""
    outputs: tuple[GeneratedFile, ...] = render_outputs()
    mismatches: list[str] = [
        output.path
        for output in outputs
        if not Path(output.path).is_file()
        or Path(output.path).read_bytes() != output.content.encode()
    ]
    assert not mismatches, ", ".join(mismatches)
    assert len(outputs) == 16
    for output in outputs:
        if output.path.endswith(".json"):
            marker: JsonValue | None = parse_json(output.content.encode()).get("obfuscation")
            assert marker is not None and marker.str_value == "xor-v1:telescreen-go-parity-fixture"
    version: str = ".".join(str(x) for x in sys.version_info[:3])
    header: str = f"# python={version} unidata={unicodedata.unidata_version}\n"
    assert all(
        output.content.startswith(header) for output in outputs if output.path.endswith(".txt")
    )
    detector: JsonValue = parse_json(Path("go/testdata/parity/detector.json").read_bytes())
    meta: JsonValue | None = detector.get("meta")
    assert meta is not None and meta.get("generated") is not None
    generated: JsonValue | None = meta.get("generated")
    assert generated is not None and generated.int_value >= 2000
    cases: JsonValue | None = detector.get("cases")
    assert cases is not None
    found: set[str] = set()
    for case in cases.items[: -generated.int_value]:
        findings: JsonValue | None = case.get("findings")
        if findings is not None:
            for finding in findings.items:
                rule: JsonValue | None = finding.get("rule_id")
                if rule is not None:
                    found.add(rule.str_value)
    assert {rule.rule_id for rule in RULES} <= found
