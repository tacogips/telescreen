"""Workflow DSL v2 notification sets: parsing, validation, resolution and JSON helpers.

Issues carry JSON paths and closed codes only; recipient values never appear in them.
"""

from __future__ import annotations

import re
from dataclasses import replace

from telescreen.core.json import JsonKind, JsonValue, json_array, json_object, json_str
from telescreen.core.vocabulary import NotificationChannel
from telescreen.ports.directory import SLACK_CHANNEL_ID
from telescreen.workflow.dsl.model import (
    API_VERSION_V2,
    CompiledWorkflow,
    EvidenceUploadStep,
    HumanTaskStep,
    NotificationSetSpec,
    NotificationTargetSpec,
    NotifyStep,
    StepSpec,
)
from telescreen.workflow.dsl.validate import ValidationIssue

V2_ONLY_TOP_LEVEL: tuple[str, ...] = ("notification_sets",)
V2_ONLY_STEP_FIELDS: tuple[str, ...] = (
    "notification_set",
    "targets",
    "template",
    "notify_set",
    "notify_targets",
)

_SET_NAME: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_TEMPLATE_NAME: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_SLACK_ID: re.Pattern[str] = re.compile(r"^[CDGUW][A-Z0-9]{8,}$")
_EMAIL_FORBIDDEN: re.Pattern[str] = re.compile(r"[\s,;<>\x00-\x1f\x7f]")
_EMAIL_MAX_LENGTH: int = 254


def read_notification_sets(
    issues: list[ValidationIssue], value: JsonValue | None, path: str
) -> tuple[NotificationSetSpec, ...]:
    """Parse the top-level ``notification_sets`` list."""
    if value is None or value.is_null():
        return ()
    if value.kind is not JsonKind.ARRAY:
        issues.append(ValidationIssue(path, "invalid_type"))
        return ()
    sets: list[NotificationSetSpec] = []
    for index, item in enumerate(value.items):
        item_path: str = f"{path}/{index}"
        if item.kind is not JsonKind.OBJECT:
            issues.append(ValidationIssue(item_path, "invalid_type"))
            continue
        name_value: JsonValue | None = item.get("name")
        name: str | None = name_value.as_str() if name_value is not None else None
        if name is None:
            issues.append(
                ValidationIssue(
                    f"{item_path}/name", "missing_field" if name_value is None else "invalid_type"
                )
            )
            name = ""
        targets_value: JsonValue | None = item.get("targets")
        if targets_value is None:
            issues.append(ValidationIssue(f"{item_path}/targets", "missing_field"))
        targets: tuple[NotificationTargetSpec, ...] = read_targets(
            issues, targets_value, f"{item_path}/targets"
        )
        sets.append(NotificationSetSpec(name, targets))
    return tuple(sets)


def read_targets(
    issues: list[ValidationIssue], value: JsonValue | None, path: str
) -> tuple[NotificationTargetSpec, ...]:
    """Parse a non-empty list of ``{recipients, channels, template?}`` targets."""
    if value is None:
        return ()
    if value.kind is not JsonKind.ARRAY:
        issues.append(ValidationIssue(path, "invalid_type"))
        return ()
    if not value.items:
        issues.append(ValidationIssue(path, "empty_list"))
        return ()
    targets: list[NotificationTargetSpec] = []
    for index, item in enumerate(value.items):
        item_path: str = f"{path}/{index}"
        if item.kind is not JsonKind.OBJECT:
            issues.append(ValidationIssue(item_path, "invalid_type"))
            continue
        recipients: tuple[str, ...] = _required_strings(
            issues, item.get("recipients"), f"{item_path}/recipients"
        )
        raw_channels: tuple[str, ...] = _required_strings(
            issues, item.get("channels"), f"{item_path}/channels"
        )
        channels: list[NotificationChannel] = []
        for channel_index, raw in enumerate(raw_channels):
            channel_path: str = f"{item_path}/channels/{channel_index}"
            try:
                channel: NotificationChannel = NotificationChannel(raw)
            except ValueError:
                issues.append(ValidationIssue(channel_path, "invalid_value"))
                continue
            if channel in channels:
                issues.append(ValidationIssue(channel_path, "invalid_value"))
                continue
            channels.append(channel)
        template: str | None = read_template_name(
            issues, item.get("template"), f"{item_path}/template"
        )
        targets.append(NotificationTargetSpec(recipients, tuple(channels), template))
    return tuple(targets)


def read_template_name(
    issues: list[ValidationIssue], value: JsonValue | None, path: str
) -> str | None:
    """Parse an optional template name reference."""
    if value is None or value.is_null():
        return None
    text: str | None = value.as_str()
    if text is None:
        issues.append(ValidationIssue(path, "invalid_type"))
        return None
    if _TEMPLATE_NAME.fullmatch(text) is None:
        issues.append(ValidationIssue(path, "invalid_value"))
        return None
    return text


def validate_recipient_for_channels(
    ref: str,
    channels: tuple[NotificationChannel, ...],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    """Check one recipient reference's form and that every channel can deliver to it."""
    if ":" not in ref:
        issues.append(ValidationIssue(path, "invalid_recipient"))
        return
    kind: str = ref.split(":", 1)[0]
    value: str = ref.split(":", 1)[1]
    if not value:
        issues.append(ValidationIssue(path, "invalid_recipient"))
        return
    if kind in ("member", "team"):
        return
    if kind == "email":
        if not _valid_email(value):
            issues.append(ValidationIssue(path, "invalid_recipient"))
        elif NotificationChannel.SLACK in channels:
            issues.append(ValidationIssue(path, "undeliverable_combination"))
        return
    if kind == "slack_channel" and _SLACK_ID.fullmatch(value) is None:
        issues.append(ValidationIssue(path, "invalid_recipient"))
        return
    if kind in ("slack_channel", "slack"):
        if NotificationChannel.EMAIL in channels:
            issues.append(ValidationIssue(path, "undeliverable_combination"))
        return
    issues.append(ValidationIssue(path, "invalid_recipient"))


def validate_targets(
    targets: tuple[NotificationTargetSpec, ...], path: str, issues: list[ValidationIssue]
) -> None:
    """Validate every recipient of every target against that target's channels."""
    for index, target in enumerate(targets):
        for recipient_index, ref in enumerate(target.recipients):
            validate_recipient_for_channels(
                ref,
                target.channels,
                f"{path}/{index}/recipients/{recipient_index}",
                issues,
            )


def notification_set_issues(workflow: CompiledWorkflow) -> list[ValidationIssue]:
    """Return set, set-reference and target issues of a typed workflow."""
    issues: list[ValidationIssue] = []
    names: list[str] = []
    for index, notification_set in enumerate(workflow.notification_sets):
        path: str = f"/notification_sets/{index}"
        if _SET_NAME.fullmatch(notification_set.name) is None:
            issues.append(ValidationIssue(f"{path}/name", "invalid_value"))
        if notification_set.name in names:
            issues.append(ValidationIssue(f"{path}/name", "duplicate_name"))
        names.append(notification_set.name)
        validate_targets(notification_set.targets, f"{path}/targets", issues)
    for index, step in enumerate(workflow.steps):
        path = f"/steps/{index}"
        if isinstance(step, NotifyStep):
            if step.notification_set is not None and step.notification_set not in names:
                issues.append(
                    ValidationIssue(f"{path}/notification_set", "unknown_notification_set")
                )
            validate_targets(step.targets, f"{path}/targets", issues)
            if workflow.api_version == API_VERSION_V2:
                for to_index, ref in enumerate(step.to):
                    validate_recipient_for_channels(
                        ref, (step.channel,), f"{path}/to/{to_index}", issues
                    )
        if isinstance(step, (HumanTaskStep, EvidenceUploadStep)):
            if step.notify_set is not None and step.notify_set not in names:
                issues.append(ValidationIssue(f"{path}/notify_set", "unknown_notification_set"))
            validate_targets(step.notify_targets, f"{path}/notify_targets", issues)
    return issues


def resolve_set_targets(workflow: CompiledWorkflow) -> CompiledWorkflow:
    """Return a copy whose set-referencing steps carry the named set's targets.

    This is the single resolver used by the loader and by ``compiled_from_json``. An unknown
    set name resolves to no targets; validation reports it.
    """
    return replace(
        workflow,
        steps=tuple(_resolve_step(step, workflow.notification_sets) for step in workflow.steps),
    )


def _resolve_step(step: StepSpec, sets: tuple[NotificationSetSpec, ...]) -> StepSpec:
    if isinstance(step, NotifyStep):
        return replace(step, set_targets=_set_targets(sets, step.notification_set))
    if isinstance(step, (HumanTaskStep, EvidenceUploadStep)):
        return replace(step, set_targets=_set_targets(sets, step.notify_set))
    return step


def _set_targets(
    sets: tuple[NotificationSetSpec, ...], name: str | None
) -> tuple[NotificationTargetSpec, ...]:
    if name is None:
        return ()
    return next((item.targets for item in sets if item.name == name), ())


def find_set(sets: tuple[NotificationSetSpec, ...], name: str) -> NotificationSetSpec | None:
    """Return a declared notification set by name."""
    return next((item for item in sets if item.name == name), None)


def targets_json(targets: tuple[NotificationTargetSpec, ...]) -> JsonValue:
    """Serialize targets in canonical order, omitting an absent template."""
    items: list[JsonValue] = []
    for target in targets:
        members: list[tuple[str, JsonValue]] = [
            ("recipients", json_array(tuple(json_str(item) for item in target.recipients))),
            ("channels", json_array(tuple(json_str(item.value) for item in target.channels))),
        ]
        if target.template is not None:
            members.append(("template", json_str(target.template)))
        items.append(json_object(members))
    return json_array(tuple(items))


def notification_sets_json(sets: tuple[NotificationSetSpec, ...]) -> JsonValue:
    """Serialize declared notification sets."""
    return json_array(
        tuple(
            json_object((("name", json_str(item.name)), ("targets", targets_json(item.targets))))
            for item in sets
        )
    )


def _required_strings(
    issues: list[ValidationIssue], value: JsonValue | None, path: str
) -> tuple[str, ...]:
    if value is None or value.kind is not JsonKind.ARRAY:
        issues.append(ValidationIssue(path, "missing_field" if value is None else "invalid_type"))
        return ()
    if not value.items:
        issues.append(ValidationIssue(path, "empty_list"))
        return ()
    result: list[str] = []
    for index, item in enumerate(value.items):
        text: str | None = item.as_str()
        if text is None:
            issues.append(ValidationIssue(f"{path}/{index}", "invalid_type"))
        else:
            result.append(text)
    return tuple(result)


def _valid_email(address: str) -> bool:
    if len(address) > _EMAIL_MAX_LENGTH or address.count("@") != 1:
        return False
    if _EMAIL_FORBIDDEN.search(address) is not None:
        return False
    local: str = address.split("@", 1)[0]
    domain: str = address.split("@", 1)[1]
    return bool(local) and bool(domain)


def channel_only_issues(workflow: CompiledWorkflow) -> list[ValidationIssue]:
    """Return ``slack_channel:`` recipients of a new definition that are not channel ids.

    Only newly uploaded or validated sources are checked: stored definitions keep loading,
    and the recipient resolver refuses user and DM ids when a notification is sent.
    """
    issues: list[ValidationIssue] = []
    for index, notification_set in enumerate(workflow.notification_sets):
        _channel_only_targets(
            notification_set.targets, f"/notification_sets/{index}/targets", issues
        )
    for index, step in enumerate(workflow.steps):
        path: str = f"/steps/{index}"
        if isinstance(step, NotifyStep):
            _channel_only_targets(step.targets, f"{path}/targets", issues)
            for to_index, ref in enumerate(step.to):
                _channel_only_ref(ref, f"{path}/to/{to_index}", issues)
        if isinstance(step, (HumanTaskStep, EvidenceUploadStep)):
            _channel_only_targets(step.notify_targets, f"{path}/notify_targets", issues)
    return issues


def channel_only_target_issues(
    targets: tuple[NotificationTargetSpec, ...], path: str
) -> list[ValidationIssue]:
    """Return channel-only issues for inline targets, such as escalation tiers."""
    issues: list[ValidationIssue] = []
    _channel_only_targets(targets, path, issues)
    return issues


def _channel_only_targets(
    targets: tuple[NotificationTargetSpec, ...], path: str, issues: list[ValidationIssue]
) -> None:
    for target_index, target in enumerate(targets):
        for recipient_index, ref in enumerate(target.recipients):
            _channel_only_ref(ref, f"{path}/{target_index}/recipients/{recipient_index}", issues)


def _channel_only_ref(ref: str, path: str, issues: list[ValidationIssue]) -> None:
    parts: tuple[str, str, str] = ref.partition(":")
    if parts[0] == "slack_channel" and SLACK_CHANNEL_ID.fullmatch(parts[2]) is None:
        issues.append(ValidationIssue(path, "invalid_recipient"))
