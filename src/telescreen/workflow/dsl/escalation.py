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
    json_object,
    json_str,
    parse_json,
    parse_yaml,
)
from telescreen.core.vocabulary import ErrorCode, NotificationChannel, SourceFormat
from telescreen.workflow.dsl._iso import format_duration
from telescreen.workflow.dsl.loader import ValidationIssue, WorkflowValidationError


@dataclass(frozen=True)
class EscalationTier:
    """One escalation assignment and notification tier."""

    after: timedelta
    assign_to: str
    notify: tuple[str, ...]
    channels: tuple[NotificationChannel, ...]


@dataclass(frozen=True)
class EscalationPolicySpec:
    """Named ordered escalation policy."""

    name: str
    tiers: tuple[EscalationTier, ...]


_RECIPIENT: re.Pattern[str] = re.compile(r"^(member|team|email|slack):.+$")


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
    notify: tuple[str, ...] = _read_string_array(value.get("notify"), f"{path}/notify", issues)
    for notify_index, recipient in enumerate(notify):
        if _RECIPIENT.fullmatch(recipient) is None:
            issues.append(ValidationIssue(f"{path}/notify/{notify_index}", "invalid_value"))
    raw_channels: tuple[str, ...] = _read_string_array(
        value.get("channels"), f"{path}/channels", issues
    )
    channels: list[NotificationChannel] = []
    for channel_index, raw in enumerate(raw_channels):
        try:
            channels.append(NotificationChannel(raw))
        except ValueError:
            issues.append(ValidationIssue(f"{path}/channels/{channel_index}", "invalid_value"))
    return EscalationTier(after, assign_to, notify, tuple(channels))


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
                json_array(
                    tuple(
                        json_object(
                            (
                                ("after", json_str(format_duration(tier.after))),
                                ("assign_to", json_str(tier.assign_to)),
                                (
                                    "notify",
                                    json_array(tuple(json_str(item) for item in tier.notify)),
                                ),
                                (
                                    "channels",
                                    json_array(
                                        tuple(json_str(item.value) for item in tier.channels)
                                    ),
                                ),
                            )
                        )
                        for tier in policy.tiers
                    )
                ),
            ),
        )
    )


def escalation_from_json(value: JsonValue) -> EscalationPolicySpec:
    """Restore a policy from its JSON representation."""
    from telescreen.core.json import dump_json

    try:
        return load_escalation_policy(dump_json(value), SourceFormat.JSON)
    except WorkflowValidationError:
        raise TelescreenError(ErrorCode.WORKFLOW_INVALID, "invalid escalation policy") from None
