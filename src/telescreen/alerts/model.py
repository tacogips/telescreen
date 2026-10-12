"""Typed alert rule values and canonical JSON conversion."""

from dataclasses import dataclass

from telescreen.core.json import (
    JsonKind,
    JsonValue,
    json_array,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.vocabulary import (
    AlertTriggerKind,
    IssuePriority,
    NotificationChannel,
    Severity,
)
from telescreen.workflow.dsl.model import NotificationTargetSpec
from telescreen.workflow.dsl.notification_sets import targets_json


@dataclass(frozen=True)
class AlertTrigger:
    """Alert event or threshold trigger configuration."""

    kind: AlertTriggerKind
    threshold: int | None = None
    window_minutes: int | None = None


@dataclass(frozen=True)
class AlertFilters:
    """Optional issue properties that narrow an alert rule."""

    min_severity: Severity | None = None
    priorities: tuple[IssuePriority, ...] = ()
    sources: tuple[str, ...] = ()
    rule_classes: tuple[str, ...] = ()
    exposures: tuple[str, ...] = ()
    owner_teams: tuple[str, ...] = ()
    detectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlertActions:
    """Notification recipients and optional workflow action."""

    targets: tuple[NotificationTargetSpec, ...] = ()
    start_workflow: str | None = None


@dataclass(frozen=True)
class AlertRule:
    """Complete rule configuration, excluding persistence metadata."""

    name: str
    trigger: AlertTrigger
    filters: AlertFilters
    actions: AlertActions
    action_interval_minutes: int = 60


def filters_json(filters: AlertFilters) -> JsonValue:
    """Serialize alert filters into canonical JSON values."""
    return json_object(
        (
            (
                "min_severity",
                json_str(filters.min_severity.value) if filters.min_severity else json_null(),
            ),
            ("priorities", _strings(tuple(item.value for item in filters.priorities))),
            ("sources", _strings(filters.sources)),
            ("rule_classes", _strings(filters.rule_classes)),
            ("exposures", _strings(filters.exposures)),
            ("owner_teams", _strings(filters.owner_teams)),
            ("detectors", _strings(filters.detectors)),
        )
    )


def actions_json(actions: AlertActions) -> JsonValue:
    """Serialize notification targets and workflow action into canonical JSON."""
    return json_object(
        (
            ("targets", targets_json(actions.targets)),
            (
                "start_workflow",
                json_str(actions.start_workflow) if actions.start_workflow else json_null(),
            ),
        )
    )


def actions_from_json(value: JsonValue) -> AlertActions:
    """Decode persisted notification targets and workflow action data."""
    target_value: JsonValue | None = value.get("targets")
    targets: list[NotificationTargetSpec] = []
    if target_value is not None and target_value.kind is JsonKind.ARRAY:
        for item in target_value.items:
            recipients: tuple[str, ...] = _read_strings(item.get("recipients"))
            channel_names: tuple[str, ...] = _read_strings(item.get("channels"))
            channels: tuple[NotificationChannel, ...] = tuple(
                NotificationChannel(name) for name in channel_names
            )
            template_value: JsonValue | None = item.get("template")
            targets.append(
                NotificationTargetSpec(
                    recipients,
                    channels,
                    template_value.as_str() if template_value is not None else None,
                )
            )
    workflow_value: JsonValue | None = value.get("start_workflow")
    return AlertActions(
        tuple(targets), workflow_value.as_str() if workflow_value is not None else None
    )


def filters_from_json(value: JsonValue) -> AlertFilters:
    """Decode persisted filter JSON into typed filters."""
    severity_value: JsonValue | None = value.get("min_severity")
    severity_text: str | None = severity_value.as_str() if severity_value is not None else None
    severity: Severity | None = Severity(severity_text) if severity_text else None
    return AlertFilters(
        severity,
        tuple(IssuePriority(item) for item in _read_strings(value.get("priorities"))),
        _read_strings(value.get("sources")),
        _read_strings(value.get("rule_classes")),
        _read_strings(value.get("exposures")),
        _read_strings(value.get("owner_teams")),
        _read_strings(value.get("detectors")),
    )


def _strings(values: tuple[str, ...]) -> JsonValue:
    return json_array(tuple(json_str(value) for value in values))


def _read_strings(value: JsonValue | None) -> tuple[str, ...]:
    if value is None or value.kind is not JsonKind.ARRAY:
        return ()
    return tuple(item.str_value for item in value.items if item.kind is JsonKind.STRING)
