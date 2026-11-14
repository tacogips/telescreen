"""Tests for versioned value fingerprints and stable identity keys."""

import pytest

from telescreen.core.errors import ConfigError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import IdentityKind, PlacementKind, RuleClass
from telescreen.detection import DetectionResult, RuleDetector, ScanItem
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.masking.seal import Masker
from telescreen.ports.collectors import OwnerIdentity, PlacementRef
from telescreen.ports.findings import MaskedFinding
from tests.support.samples import Sample, email_address


def test_single_key_outputs_are_byte_identical_to_v1() -> None:
    """The one-key constructor preserves every v1 HMAC output."""
    secret: SecretValue = SecretValue("k" * 32)
    legacy: Fingerprinter = Fingerprinter.from_secret(secret)
    versioned: Fingerprinter = Fingerprinter.from_versions(((1, secret),), 1)
    assert versioned.fingerprint("rule", "value", RuleClass.CREDENTIAL) == legacy.fingerprint(
        "rule", "value", RuleClass.CREDENTIAL
    )
    assert versioned.resource_key("gdrive", "raw-locator") == legacy.resource_key(
        "gdrive", "raw-locator"
    )
    assert versioned.external("slack", "external-fingerprint") == legacy.external(
        "slack", "external-fingerprint"
    )
    assert versioned.key_id == legacy.key_id
    assert versioned.key_version == 1


def test_rotation_changes_value_key_but_keeps_identity_key() -> None:
    """Active value key rotation leaves v1 resource identity unchanged."""
    version_one: SecretValue = SecretValue("a" * 32)
    version_two: SecretValue = SecretValue("b" * 32)
    old: Fingerprinter = Fingerprinter.from_secret(version_one)
    rotated: Fingerprinter = Fingerprinter.from_versions(((1, version_one), (2, version_two)), 2)
    assert rotated.resource_key("drive", "locator") == old.resource_key("drive", "locator")
    assert rotated.fingerprint("rule", "value", RuleClass.CREDENTIAL) != old.fingerprint(
        "rule", "value", RuleClass.CREDENTIAL
    )
    assert rotated.key_version == 2


def test_versioned_fingerprinter_rejects_short_keys() -> None:
    """Every configured key must meet the fingerprint key length requirement."""
    with pytest.raises(ConfigError):
        Fingerprinter.from_versions(((1, SecretValue("short")),), 1)


def test_owner_and_placement_keys_hide_raw_values_and_normalize_email() -> None:
    """Identity references are keyed, stable, and repr-safe."""
    fingerprinter: Fingerprinter = Fingerprinter.from_secret(SecretValue("c" * 32))
    owner_upper: OwnerIdentity = OwnerIdentity(IdentityKind.EMAIL, "A@Example.com")
    owner_lower: OwnerIdentity = OwnerIdentity(IdentityKind.EMAIL, "a@example.com")
    assert fingerprinter.owner_ref(owner_upper) == fingerprinter.owner_ref(owner_lower)
    placement: PlacementRef = PlacementRef(PlacementKind.GDRIVE_FOLDER, "private-folder-id")
    assert fingerprinter.placement_key(placement.kind, placement.value).startswith("gdrive_folder:")
    assert "A@Example.com" not in repr(owner_upper)
    assert "private-folder-id" not in repr(placement)


def test_masker_records_active_fingerprint_key_version() -> None:
    """Sealed findings identify which active fingerprint key created them."""
    sample: Sample = email_address()
    item: ScanItem = ScanItem("item", "test", "masked-locator", sample.value)
    fingerprinter: Fingerprinter = Fingerprinter.from_versions(
        ((1, SecretValue("d" * 32)), (2, SecretValue("e" * 32))), 2
    )
    findings: list[MaskedFinding] = Masker(fingerprinter, Redactor.default()).seal(
        item, DetectionResult("item", tuple(RuleDetector().scan_text(sample.value)))
    )
    assert findings
    assert all(finding.fingerprint_key_version == 2 for finding in findings)
