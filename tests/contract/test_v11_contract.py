"""Contract 1.1 exposure schema compatibility tests."""

from telescreen.contracts.validator import validate
from telescreen_police_sdk import _json
from telescreen_police_sdk.models import ResourceExposure, ResultEnvelope, ResultStatus, Visibility


def test_exposure_schema_rejects_unknown_members_invalid_values_and_too_many_items() -> None:
    """Keep exposure items closed and enforce the enum and array limit."""
    extra_member: bytes = (
        b'{"contract_version":"1.1","task_id":"task","status":"completed",'
        b'"resource_exposures":[{"locator":"docs/example.txt","visibility":"public",'
        b'"extra":true}]}'
    )
    invalid_visibility: bytes = (
        b'{"contract_version":"1.1","task_id":"task","status":"completed",'
        b'"resource_exposures":[{"locator":"docs/example.txt","visibility":"world"}]}'
    )
    assert any(
        error.keyword == "additionalProperties"
        for error in validate("result-envelope.schema.json", extra_member)
    )
    assert any(
        error.keyword == "enum"
        for error in validate("result-envelope.schema.json", invalid_visibility)
    )
    too_many: _json.Json = _json.obj(
        (
            ("contract_version", _json.string("1.1")),
            ("task_id", _json.string("task")),
            ("status", _json.string("completed")),
            (
                "resource_exposures",
                _json.array(
                    tuple(
                        ResourceExposure("docs/example.txt", Visibility.PUBLIC).to_json()
                        for _ in range(1001)
                    )
                ),
            ),
        )
    )
    assert any(
        error.keyword == "maxItems"
        for error in validate("result-envelope.schema.json", _json.dump(too_many).encode())
    )


def test_v10_envelope_remains_valid_without_exposures() -> None:
    """Accept the original 1.0 envelope shape."""
    assert (
        validate(
            "result-envelope.schema.json",
            b'{"contract_version":"1.0","task_id":"task","status":"completed"}',
        )
        == []
    )


def test_sdk_exposure_envelope_matches_shared_schema() -> None:
    """Validate the SDK's serialized 1.1 exposure envelope."""
    exposure: ResourceExposure = ResourceExposure("docs/example.txt", Visibility.PUBLIC)
    envelope: ResultEnvelope = ResultEnvelope(
        "task", ResultStatus.COMPLETED, resource_exposures=(exposure,)
    )
    assert validate("result-envelope.schema.json", _json.dump(envelope.to_json()).encode()) == []
