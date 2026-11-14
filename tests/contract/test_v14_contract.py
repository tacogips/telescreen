"""Police contract 1.2 resource inventory validation tests."""

from pathlib import Path

from telescreen.contracts.validator import validate

_EXAMPLE: Path = Path("src/telescreen/contracts/police/v1/examples/result-envelope-1.2.json")


def test_v12_inventory_example_and_older_envelopes_validate() -> None:
    """Accept the 1.2 example plus legacy 1.0 and 1.1 result envelopes."""
    assert validate("result-envelope.schema.json", _EXAMPLE.read_bytes()) == []
    assert (
        validate(
            "result-envelope.schema.json",
            b'{"contract_version":"1.0","task_id":"task","status":"completed"}',
        )
        == []
    )
    assert (
        validate(
            "result-envelope.schema.json",
            b'{"contract_version":"1.1","task_id":"task","status":"completed",'
            b'"resource_exposures":[]}',
        )
        == []
    )


def test_inventory_schema_accepts_locator_only_and_rejects_conflicts() -> None:
    """Require a locator and reject simultaneous count and fingerprint forms."""
    valid: bytes = (
        b'{"contract_version":"1.2","task_id":"task","status":"completed",'
        b'"resource_inventories":[{"locator":"warehouse/table"}]}'
    )
    invalid: bytes = (
        b'{"contract_version":"1.2","task_id":"task","status":"completed",'
        b'"resource_inventories":[{"locator":"warehouse/table",'
        b'"item_count":3,"item_fingerprints":[]}]}'
    )
    malformed: bytes = (
        b'{"contract_version":"1.2","task_id":"task","status":"completed",'
        b'"resource_inventories":[{"locator":"warehouse/table",'
        b'"item_fingerprints":["bad"]}]}'
    )
    assert validate("result-envelope.schema.json", valid) == []
    assert any(error.keyword == "not" for error in validate("result-envelope.schema.json", invalid))
    assert any(
        error.keyword == "pattern" for error in validate("result-envelope.schema.json", malformed)
    )
