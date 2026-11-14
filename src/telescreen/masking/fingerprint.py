"""Keyed fingerprints for sensitive values and resource locators."""

import hashlib
import hmac
import unicodedata
from dataclasses import dataclass, field

from telescreen.core.errors import ConfigError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import IdentityKind, PlacementKind, RuleClass
from telescreen.ports.collectors import OwnerIdentity


@dataclass(frozen=True)
class Fingerprinter:
    """Create domain-separated HMAC fingerprints without retaining raw values."""

    key: bytes = field(repr=False)
    key_id: str
    key_version: int = 1
    identity_secret: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Reject keys too short for the configured fingerprint policy."""
        if len(self.key) < 32:
            raise ConfigError("fingerprint key must contain at least 32 bytes")

    @staticmethod
    def from_secret(secret: SecretValue) -> "Fingerprinter":
        """Build a fingerprinter from a resolved secret value."""
        key: bytes = secret.value.encode("utf-8")
        if len(key) < 32:
            raise ConfigError("fingerprint key must contain at least 32 bytes")
        key_id: str = hashlib.sha256(key).hexdigest()[:8]
        return Fingerprinter(key=key, key_id=key_id, identity_secret=key)

    @staticmethod
    def from_versions(keys: tuple[tuple[int, SecretValue], ...], active: int) -> "Fingerprinter":
        """Build a fingerprinter with versioned value keys and a v1 identity key."""
        resolved: tuple[tuple[int, bytes], ...] = tuple(
            (version, secret.value.encode("utf-8")) for version, secret in keys
        )
        if any(len(key) < 32 for _, key in resolved):
            raise ConfigError("fingerprint key must contain at least 32 bytes")
        active_key: bytes | None = next(
            (key for version, key in resolved if version == active), None
        )
        identity_key: bytes | None = next((key for version, key in resolved if version == 1), None)
        if active_key is None or identity_key is None:
            raise ConfigError("fingerprint key versions are not configured")
        key_id: str = hashlib.sha256(active_key).hexdigest()[:8]
        return Fingerprinter(
            key=active_key,
            key_id=key_id,
            key_version=active,
            identity_secret=identity_key,
        )

    def fingerprint(self, rule_id: str, raw: str, rule_class: RuleClass) -> str:
        """Fingerprint a value with rule-specific domain separation."""
        normalized: str = self.normalize_for_fingerprint(raw, rule_class)
        message: bytes = f"v1\0{rule_id}\0{normalized}".encode()
        return hmac.new(self.key, message, hashlib.sha256).hexdigest()

    def resource_key(self, source: str, raw_locator: str) -> str:
        """Create a stable keyed resource identifier."""
        message: bytes = f"resource\0{source}\0{raw_locator}".encode()
        identity_key: bytes = self.identity_secret if self.identity_secret is not None else self.key
        return hmac.new(identity_key, message, hashlib.sha256).hexdigest()

    def placement_key(self, kind: PlacementKind, value: str) -> str:
        """Create a keyed placement reference without retaining its source value."""
        message: bytes = f"placement\0{kind}\0{value}".encode()
        identity_key: bytes = self.identity_secret if self.identity_secret is not None else self.key
        digest: str = hmac.new(identity_key, message, hashlib.sha256).hexdigest()
        return f"{kind}:{digest}"

    def owner_ref(self, identity: OwnerIdentity) -> str:
        """Create a keyed owner reference from a transient provider identity."""
        normalized: str = identity.value
        if identity.kind is IdentityKind.EMAIL:
            normalized = unicodedata.normalize("NFKC", normalized).lower()
        elif identity.kind is IdentityKind.GITHUB_LOGIN:
            normalized = normalized.lower()
        message: bytes = f"identity\0{identity.kind}\0{normalized}".encode()
        identity_key: bytes = self.identity_secret if self.identity_secret is not None else self.key
        digest: str = hmac.new(identity_key, message, hashlib.sha256).hexdigest()
        return f"{identity.kind}:{digest}"

    def external(self, source: str, external_fingerprint: str) -> str:
        """Re-key a fingerprint supplied by an external detector."""
        message: bytes = f"ext\0{source}\0{external_fingerprint}".encode()
        return hmac.new(self.key, message, hashlib.sha256).hexdigest()

    @staticmethod
    def normalize_for_fingerprint(raw: str, rule_class: RuleClass) -> str:
        """Normalize equivalent forms before fingerprinting."""
        normalized: str = unicodedata.normalize("NFKC", raw)
        if rule_class in (RuleClass.PAYMENT_CARD, RuleClass.NATIONAL_ID, RuleClass.PHONE):
            return normalized.replace(" ", "").replace("-", "")
        if rule_class is RuleClass.EMAIL:
            return normalized.lower()
        return normalized
