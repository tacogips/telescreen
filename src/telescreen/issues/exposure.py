"""Exposure merging and effective severity helpers for issue recording."""

from telescreen.core.json import JsonValue, dump_json, json_bool, json_int, json_object, json_str
from telescreen.core.vocabulary import (
    CREDENTIAL_CLASSES,
    PII_CLASSES,
    RuleClass,
    Severity,
    Visibility,
    severity_rank,
)
from telescreen.ports.collectors import Exposure


def merge_visibility(stored: Visibility, new: Visibility | None) -> Visibility:
    """Keep known visibility when the latest observation is unknown or absent."""
    if new is None or new is Visibility.UNKNOWN:
        return stored
    return new


def severity_floor(rule_class: RuleClass, visibility: Visibility) -> Severity | None:
    """Return the exposure severity floor for credential and PII classes."""
    if visibility not in (Visibility.PUBLIC, Visibility.ANYONE_WITH_LINK):
        return None
    if rule_class in CREDENTIAL_CLASSES:
        return Severity.CRITICAL
    if rule_class in PII_CLASSES:
        return Severity.HIGH
    return None


def effective_severity(base: Severity, floor: Severity | None) -> Severity:
    """Select the more severe of the rule severity and exposure floor."""
    if floor is None:
        return base
    return floor if severity_rank(floor) > severity_rank(base) else base


def exposure_json(exposure: Exposure) -> str:
    """Serialize only count and enum exposure data as canonical JSON."""
    members: list[tuple[str, JsonValue]] = [
        ("visibility", json_str(exposure.visibility.value)),
        ("external_principal_count", json_int(exposure.external_principal_count)),
        ("shared_drive", json_bool(exposure.shared_drive)),
        ("is_fork", json_bool(exposure.is_fork)),
    ]
    if exposure.channel_kind is not None:
        members.append(("channel_kind", json_str(exposure.channel_kind.value)))
    if exposure.repo_visibility is not None:
        members.append(("repo_visibility", json_str(exposure.repo_visibility.value)))
    return dump_json(json_object(members))
