"""Keyed fingerprints for sensitive values and resource locators."""

import hashlib
import hmac
import unicodedata
from dataclasses import dataclass, field

from telescreen.core.errors import ConfigError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import RuleClass


@dataclass(frozen=True)
class Fingerprinter:
    """Create domain-separated HMAC fingerprints without retaining raw values."""

    key: bytes = field(repr=False)
    key_id: str

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
        return Fingerprinter(key=key, key_id=key_id)

    def fingerprint(self, rule_id: str, raw: str, rule_class: RuleClass) -> str:
        """Fingerprint a value with rule-specific domain separation."""
        normalized: str = self.normalize_for_fingerprint(raw, rule_class)
        message: bytes = f"v1\0{rule_id}\0{normalized}".encode()
        return hmac.new(self.key, message, hashlib.sha256).hexdigest()

    def resource_key(self, source: str, raw_locator: str) -> str:
        """Create a stable keyed resource identifier."""
        message: bytes = f"resource\0{source}\0{raw_locator}".encode()
        return hmac.new(self.key, message, hashlib.sha256).hexdigest()

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
