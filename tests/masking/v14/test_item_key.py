"""Stable, domain-separated inventory item fingerprints."""

from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import RuleClass
from telescreen.masking.fingerprint import Fingerprinter


def test_item_fingerprint_is_stable_separate_and_rotation_safe() -> None:
    """Fingerprint item keys with the stable identity key and a separate domain."""
    first: Fingerprinter = Fingerprinter.from_secret(SecretValue("a" * 32))
    rotated: Fingerprinter = Fingerprinter.from_versions(
        ((1, SecretValue("a" * 32)), (2, SecretValue("b" * 32))), 2
    )
    value: str = first.item_key("resource-a", "private-item-key")
    assert len(value) == 64
    assert value == first.item_key("resource-a", "private-item-key")
    assert value != first.item_key("resource-b", "private-item-key")
    assert value != first.resource_key("resource-a", "private-item-key")
    assert value != first.fingerprint("rule", "private-item-key", RuleClass.TOKEN)
    assert value == rotated.item_key("resource-a", "private-item-key")
    assert "private-item-key" not in value
