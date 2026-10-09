"""Police contract schema and non-disclosure checks."""

import json
import re
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from telescreen.contracts.validator import SchemaError, load_schema, openapi_document, validate
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode

_CONTRACT_PATH: Path = Path("src/telescreen/contracts/police/v1")
_EXAMPLES_PATH: Path = _CONTRACT_PATH / "examples"


def test_unknown_schema_name_is_not_found_without_echo() -> None:
    """Reject unknown schema names with a static, non-echoing error."""
    requested_name: str = "unknown-sensitive-name.schema.json"
    with pytest.raises(TelescreenError) as captured:
        load_schema(requested_name)
    assert captured.value.code is ErrorCode.NOT_FOUND
    assert requested_name not in captured.value.detail


def test_every_schema_is_valid_and_every_example_validates() -> None:
    """Check schema syntax and every shared example."""
    for schema_path in sorted(_CONTRACT_PATH.glob("*.schema.json")):
        Draft202012Validator.check_schema(json.loads(schema_path.read_text(encoding="utf-8")))
    for example_path in sorted(_EXAMPLES_PATH.glob("*.json")):
        stem: str = example_path.stem
        schema_stem: str = stem.split("-", maxsplit=1)[0] if stem.startswith("manifest-") else stem
        if stem.startswith("result-envelope-"):
            schema_stem = "result-envelope"
        errors: list[SchemaError] = validate(
            f"{schema_stem}.schema.json", example_path.read_bytes()
        )
        assert errors == [], f"{example_path.name}: {errors}"


def test_manifest_push_requires_endpoint() -> None:
    """Require an endpoint URL for push workers."""
    errors: list[SchemaError] = validate(
        "manifest.schema.json",
        b'{"name":"x","version":"1","contract_version":"1.0","mode":"push","task_types":["detect"],"labels":[],"concurrency":1}',
    )
    assert any(error.keyword == "required" for error in errors)


def test_failed_result_requires_error() -> None:
    """Require a safe error body when a task fails."""
    errors: list[SchemaError] = validate(
        "result-envelope.schema.json",
        b'{"contract_version":"1.0","task_id":"task","status":"failed"}',
    )
    assert any(error.keyword == "required" for error in errors)


def test_validation_errors_never_expose_instance_value() -> None:
    """Return only location and keyword for invalid sensitive-looking data."""
    sample: str = "DO_NOT_LEAK_" + "X" * 201
    errors: list[SchemaError] = validate(
        "result-envelope.schema.json",
        json.dumps(
            {
                "contract_version": "1.0",
                "task_id": "task",
                "status": "completed",
                "findings": [{"masked_snippet": sample}],
            }
        ).encode(),
    )
    rendered: str = repr(errors)
    assert any(error.keyword == "maxLength" for error in errors)
    assert sample not in rendered


def test_contract_version_pattern_and_malformed_json() -> None:
    """Reject unsupported major versions and return one safe JSON error."""
    errors: list[SchemaError] = validate(
        "task-envelope.schema.json",
        b'{"contract_version":"2.0","task_id":"task","task_type":"detect","attempt":1,"issued_at":"2026-01-01T00:00:00Z","deadline":"2026-01-01T00:05:00Z","labels":[],"input":{}}',
    )
    assert any(error.keyword == "pattern" for error in errors)
    assert validate("result-envelope.schema.json", b"{bad") == [SchemaError("", "json")]


def test_openapi_and_package_resources_reference_existing_files() -> None:
    """Check OpenAPI references and importlib resource loading."""
    document: str = openapi_document()
    assert json.loads(document)["openapi"] == "3.1.0"
    for schema_path in _CONTRACT_PATH.glob("*.schema.json"):
        assert load_schema(schema_path.name)
    assert files("telescreen.contracts").joinpath("police", "v1", "openapi.json").is_file()
    references: list[str] = re.findall(r'"\$ref"\s*:\s*"([^"]+)"', document)
    external_references: list[str] = [
        reference for reference in references if not reference.startswith("#/")
    ]
    assert external_references
    for reference in external_references:
        assert (_CONTRACT_PATH / reference.removeprefix("./")).is_file()
