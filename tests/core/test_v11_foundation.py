"""Tests for the shared v1.1 vocabulary and settings foundation."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from telescreen.core.config import FingerprintKeyRef, TelescreenSettings, load_settings
from telescreen.core.errors import ConfigError
from telescreen.core.risk_weights import RiskWeights, parse_risk_weights
from telescreen.core.vocabulary import (
    CREDENTIAL_CLASSES,
    OPEN_STATUSES,
    PII_CLASSES,
    RECONCILED_STATUSES,
    IssueEventKind,
    IssueStatus,
    RuleClass,
    Severity,
    Visibility,
    severity_rank,
    visibility_rank,
)
from telescreen.ports.issues import IssueEvent
from telescreen.workflow.dsl.model import TriggerMatch
from telescreen.workflow.triggers.service import _matches


@dataclass(frozen=True)
class FakeEnv:
    """Small read-only environment for configuration tests."""

    values: tuple[tuple[str, str], ...] = ()

    def get(self, key: str, /) -> str | None:
        """Return a configured environment value."""
        return next((value for name, value in self.values if name == key), None)


def _env(*values: tuple[str, str]) -> FakeEnv:
    """Prefix setting names and return a compact fake environment."""
    return FakeEnv(tuple((f"TELESCREEN_{name}", value) for name, value in values))


def test_fingerprint_key_settings_keep_legacy_and_select_active_version() -> None:
    """Legacy and versioned fingerprint key settings map to the shared fields."""
    legacy: TelescreenSettings = load_settings(_env(("FINGERPRINT_KEY", "env:LEGACY")))
    assert legacy.fingerprint_keys == (FingerprintKeyRef(1, "env:LEGACY"),)
    assert legacy.fingerprint_active_version == 1
    versioned: TelescreenSettings = load_settings(_env(("FINGERPRINT_KEYS", "1=env:A,2=env:B")))
    assert versioned.fingerprint_keys == (
        FingerprintKeyRef(1, "env:A"),
        FingerprintKeyRef(2, "env:B"),
    )
    assert versioned.fingerprint_active_version == 2
    assert versioned.fingerprint_key_ref == "env:A"


@pytest.mark.parametrize(
    "values",
    (
        (("FINGERPRINT_KEY", "env:A"), ("FINGERPRINT_KEYS", "1=env:B")),
        (("FINGERPRINT_KEY", "env:A"), ("FINGERPRINT_ACTIVE_VERSION", "2")),
        (("FINGERPRINT_KEYS", "2=env:B"),),
        (("FINGERPRINT_KEYS", "1=env:A,1=env:B"),),
        (("FINGERPRINT_KEYS", "0=env:A,1=env:B"),),
        (("FINGERPRINT_KEYS", "1=env:A,2=env:B"), ("FINGERPRINT_ACTIVE_VERSION", "3")),
    ),
)
def test_invalid_fingerprint_key_configuration_is_rejected(
    values: tuple[tuple[str, str], ...],
) -> None:
    """Ambiguous, incomplete, and unconfigured key versions fail closed."""
    with pytest.raises(ConfigError):
        load_settings(_env(*values))


def test_risk_weights_and_digest_settings_are_validated() -> None:
    """Risk overrides and digest cron settings parse to typed values."""
    weights: RiskWeights = parse_risk_weights("severity.critical=0.9,age.over_30d=0.95")
    assert weights.severity_critical == 0.9
    assert weights.age_over_30d == 0.95
    with pytest.raises(ConfigError):
        parse_risk_weights("unknown.value=0.9")
    with pytest.raises(ConfigError):
        parse_risk_weights("severity.critical=0")
    with pytest.raises(ConfigError):
        parse_risk_weights("severity.critical=1.5")
    with pytest.raises(ConfigError):
        parse_risk_weights("severity.critical=invalid")
    assert load_settings(_env(("DIGEST_CRON", ""))).digest_cron is None
    with pytest.raises(ConfigError):
        load_settings(_env(("DIGEST_CRON", "bad")))


def test_new_settings_normalize_internal_domains_and_defaults() -> None:
    """New settings use design defaults and normalize operator domains."""
    settings: TelescreenSettings = load_settings(
        _env(("INTERNAL_DOMAINS", " Example.COM ,example.org"))
    )
    assert settings.internal_domains == ("example.com", "example.org")
    assert settings.digest_cron == "0 9 * * 1"
    assert settings.health_failure_threshold == 3
    assert settings.alert_unscannable_public is True
    assert settings.owner_routing is False


def test_vocabulary_status_groups_ranks_and_critical_trigger() -> None:
    """Status groups and total severity/visibility ranks match the design."""
    assert severity_rank(Severity.CRITICAL) > severity_rank(Severity.HIGH)
    assert severity_rank(Severity.HIGH) > severity_rank(Severity.MEDIUM)
    assert visibility_rank(Visibility.PUBLIC) == 6
    assert visibility_rank(Visibility.PRIVATE) == 1
    assert visibility_rank(Visibility.UNKNOWN) is None
    assert OPEN_STATUSES == (
        IssueStatus.UNRESOLVED,
        IssueStatus.ACKNOWLEDGED,
        IssueStatus.IN_PROGRESS,
        IssueStatus.REGRESSED,
        IssueStatus.PENDING_VERIFICATION,
        IssueStatus.ESCALATING,
        IssueStatus.ARCHIVED,
    )
    assert RECONCILED_STATUSES == OPEN_STATUSES + (IssueStatus.ALLOWED,)
    assert tuple(item.value for item in CREDENTIAL_CLASSES) == (
        "credential",
        "token",
        "private_key",
        "password",
    )
    assert tuple(item.value for item in PII_CLASSES) == (
        "payment_card",
        "national_id",
        "email",
        "phone",
        "address",
    )
    event: IssueEvent = IssueEvent(
        IssueEventKind.ISSUE_CREATED,
        "event",
        "issue",
        "slack",
        RuleClass.CREDENTIAL,
        Severity.CRITICAL,
        datetime.now(UTC),
    )
    assert _matches(TriggerMatch(min_severity=Severity.HIGH), event) is True
