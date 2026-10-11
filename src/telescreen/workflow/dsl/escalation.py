"""Escalation policy parser and JSON round-trip helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from telescreen.core.durations import parse_duration
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    json_array,
    json_int,
    json_object,
    json_str,
    parse_json,
    parse_yaml,
)
from telescreen.core.vocabulary import ErrorCode, NotificationChannel, SourceFormat
from telescreen.ports.directory import SLACK_CHANNEL_ID
from telescreen.workflow.dsl._iso import format_duration
from telescreen.workflow.dsl.loader import ValidationIssue, WorkflowValidationError
from telescreen.workflow.dsl.model import NotificationTargetSpec
from telescreen.workflow.dsl.notification_sets import read_targets, targets_json, validate_targets


@dataclass(frozen=True)
class EscalationTier:
    """One escalation assignment and notification tier."""

    after: timedelta
    assign_to: str
    notify: tuple[str, ...]
    channels: tuple[NotificationChannel, ...]
    min_risk: int | None = None
    notification_set: str | None = None
    targets: tuple[NotificationTargetSpec, ...] = ()


@dataclass(frozen=True)
class EscalationPolicySpec:
    """Named ordered escalation policy."""

    name: str
    tiers: tuple[EscalationTier, ...]


_RECIPIENT: re.Pattern[str] = re.compile(r"^(member|team|email|slack|slack_channel):.+$")
_SET_NAME: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def load_escalation_policy(text: str, source_format: SourceFormat) -> EscalationPolicySpec:
    """Parse and validate an escalation policy without including values in errors."""
    try:
        value: JsonValue = (
            parse_yaml(text) if source_format is SourceFormat.YAML else parse_json(text)
        )
    except TelescreenError:
        raise WorkflowValidationError((ValidationIssue("", "invalid_document"),)) from None
    if value.kind is not JsonKind.OBJECT:
        raise WorkflowValidationError((ValidationIssue("", "invalid_document"),))
    name_value: JsonValue | None = value.get("name")
    name: str | None = name_value.as_str() if name_value is not None else None
    tiers_value: JsonValue | None = value.get("tiers")
    issues: list[ValidationIssue] = []
    if name is None:
        issues.append(ValidationIssue("/name", "missing_field"))
        name = ""
    if tiers_value is None or tiers_value.kind is not JsonKind.ARRAY:
        issues.append(ValidationIssue("/tiers", "invalid_type"))
        tiers: tuple[EscalationTier, ...] = ()
    else:
        parsed: list[EscalationTier] = []
        for index, tier_value in enumerate(tiers_value.items):
            parsed.append(_tier(tier_value, index, issues))
        tiers = tuple(parsed)
    if issues:
        raise WorkflowValidationError(tuple(issues))
    return EscalationPolicySpec(name, tiers)


def _tier(value: JsonValue, index: int, issues: list[ValidationIssue]) -> EscalationTier:
    path: str = f"/tiers/{index}"
    if value.kind is not JsonKind.OBJECT:
        issues.append(ValidationIssue(path, "invalid_type"))
        return EscalationTier(timedelta(0), "", (), ())
    min_risk_value: JsonValue | None = value.get("min_risk")
    min_risk: int | None = min_risk_value.as_int() if min_risk_value is not None else None
    if min_risk_value is not None and min_risk is None:
        issues.append(ValidationIssue(f"{path}/min_risk", "invalid_type"))
    elif min_risk is not None and not 0 <= min_risk <= 100:
        issues.append(ValidationIssue(f"{path}/min_risk", "invalid_value"))
    after_value: JsonValue | None = value.get("after")
    after_text: str | None = after_value.as_str() if after_value is not None else None
    try:
        after: timedelta = parse_duration(after_text or "")
    except TelescreenError:
        issues.append(ValidationIssue(f"{path}/after", "invalid_duration"))
        after = timedelta(0)
    assign_value: JsonValue | None = value.get("assign_to")
    assign_to: str | None = assign_value.as_str() if assign_value is not None else None
    if assign_to is None:
        issues.append(ValidationIssue(f"{path}/assign_to", "missing_field"))
        assign_to = ""
    if _RECIPIENT.fullmatch(assign_to) is None:
        issues.append(ValidationIssue(f"{path}/assign_to", "invalid_value"))
    notify_value: JsonValue | None = value.get("notify")
    notify: tuple[str, ...] = (
        _read_string_array(notify_value, f"{path}/notify", issues)
        if notify_value is not None
        else ()
    )
    for notify_index, recipient in enumerate(notify):
        if _RECIPIENT.fullmatch(recipient) is None or not _valid_slack_channel(recipient):
            issues.append(ValidationIssue(f"{path}/notify/{notify_index}", "invalid_value"))
    channels_value: JsonValue | None = value.get("channels")
    if notify and channels_value is None:
        issues.append(ValidationIssue(f"{path}/channels", "missing_field"))
    raw_channels: tuple[str, ...] = (
        _read_string_array(channels_value, f"{path}/channels", issues)
        if channels_value is not None
        else ()
    )
    channels: list[NotificationChannel] = []
    for channel_index, raw in enumerate(raw_channels):
        try:
            channels.append(NotificationChannel(raw))
        except ValueError:
            issues.append(ValidationIssue(f"{path}/channels/{channel_index}", "invalid_value"))
    set_value: JsonValue | None = value.get("notification_set")
    notification_set: str | None = set_value.as_str() if set_value is not None else None
    if set_value is not None and (
        notification_set is None or _SET_NAME.fullmatch(notification_set) is None
    ):
        issues.append(ValidationIssue(f"{path}/notification_set", "invalid_value"))
        notification_set = None
    targets: tuple[NotificationTargetSpec, ...] = read_targets(
        issues, value.get("targets"), f"{path}/targets"
    )
    validate_targets(targets, f"{path}/targets", issues)
    if notify_value is None and value.get("targets") is None and set_value is None:
        issues.append(ValidationIssue(path, "missing_field"))
    return EscalationTier(
        after, assign_to, notify, tuple(channels), min_risk, notification_set, targets
    )


def _read_string_array(
    value: JsonValue | None, path: str, issues: list[ValidationIssue]
) -> tuple[str, ...]:
    if value is None or value.kind is not JsonKind.ARRAY:
        issues.append(ValidationIssue(path, "invalid_type"))
        return ()
    result: list[str] = []
    for index, item in enumerate(value.items):
        text: str | None = item.as_str()
        if text is None:
            issues.append(ValidationIssue(f"{path}/{index}", "invalid_type"))
        else:
            result.append(text)
    return tuple(result)


def escalation_to_json(policy: EscalationPolicySpec) -> JsonValue:
    """Serialize an escalation policy to closed JSON values."""
    return json_object(
        (
            ("name", json_str(policy.name)),
            (
                "tiers",
                json_array(tuple(_tier_json(tier) for tier in policy.tiers)),
            ),
        )
    )


def _tier_json(tier: EscalationTier) -> JsonValue:
    """Serialize one tier while omitting the optional threshold when absent."""
    members: list[tuple[str, JsonValue]] = [
        ("after", json_str(format_duration(tier.after))),
        ("assign_to", json_str(tier.assign_to)),
        ("notify", json_array(tuple(json_str(item) for item in tier.notify))),
        ("channels", json_array(tuple(json_str(item.value) for item in tier.channels))),
    ]
    if tier.min_risk is not None:
        members.append(("min_risk", json_int(tier.min_risk)))
    if tier.notification_set is not None:
        members.append(("notification_set", json_str(tier.notification_set)))
    if tier.targets:
        members.append(("targets", targets_json(tier.targets)))
    return json_object(members)


def escalation_from_json(value: JsonValue) -> EscalationPolicySpec:
    """Restore a policy from its JSON representation."""
    from telescreen.core.json import dump_json

    try:
        return load_escalation_policy(dump_json(value), SourceFormat.JSON)
    except WorkflowValidationError:
        raise TelescreenError(ErrorCode.WORKFLOW_INVALID, "invalid escalation policy") from None


def _valid_slack_channel(recipient: str) -> bool:
    """Return False for a slack_channel recipient whose id is not a channel id."""
    parts: tuple[str, str, str] = recipient.partition(":")
    return parts[0] != "slack_channel" or SLACK_CHANNEL_ID.fullmatch(parts[2]) is not None
