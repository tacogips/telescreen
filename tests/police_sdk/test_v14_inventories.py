"""Police SDK resource inventory serialization and masking tests."""

from telescreen.contracts.validator import validate
from telescreen_police_sdk import _json
from telescreen_police_sdk.masking import item_fingerprint
from telescreen_police_sdk.models import ResourceInventory, ResultEnvelope, ResultStatus


def test_resource_inventory_round_trip_and_schema_validation() -> None:
    """Serialize, validate, and parse both fingerprint and count reports."""
    inventories: tuple[ResourceInventory, ...] = (
        ResourceInventory("docs/example.txt", ("a" * 64,), kind="file"),
        ResourceInventory("warehouse/customers", item_count=17, kind="table"),
    )
    envelope: ResultEnvelope = ResultEnvelope(
        "task-inventory", ResultStatus.COMPLETED, resource_inventories=inventories
    )
    payload: bytes = _json.dump(envelope.to_json()).encode()
    assert validate("result-envelope.schema.json", payload) == []
    assert ResultEnvelope.from_json(_json.parse(payload)).resource_inventories == inventories


def test_empty_inventory_member_is_omitted_and_item_fingerprint_is_stable() -> None:
    """Omit empty inventory arrays and provide stable worker fingerprints."""
    envelope: ResultEnvelope = ResultEnvelope("task", ResultStatus.COMPLETED)
    assert all(member.name != "resource_inventories" for member in envelope.to_json().members)
    assert len(item_fingerprint("item", b"x" * 32)) == 64
    assert item_fingerprint("item", b"x" * 32) == item_fingerprint("item", b"x" * 32)
