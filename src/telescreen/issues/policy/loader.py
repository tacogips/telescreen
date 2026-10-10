"""Parse and validate placement policies without echoing source values."""

from __future__ import annotations

import re
from dataclasses import dataclass

from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    json_array,
    json_bool,
    json_int,
    json_null,
    json_object,
    json_str,
    parse_json,
    parse_yaml,
)
from telescreen.core.vocabulary import (
    CollectorKind,
    ErrorCode,
    PolicyEffect,
    RuleClass,
    SourceFormat,
    Visibility,
)
from telescreen.issues.policy.model import PolicyDefinition, PolicyLocations
from telescreen.workflow.dsl.validate import ValidationIssue

_NAME: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_OWNER: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class PolicyValidationError(TelescreenError):
    """Policy diagnostics containing only paths and stable codes."""

    issues: tuple[ValidationIssue, ...]

    def __init__(
        self, issues: tuple[ValidationIssue, ...], code: ErrorCode = ErrorCode.VALIDATION_ERROR
    ) -> None:
        """Create a static policy validation error."""
        self.issues = issues
        super().__init__(code, "policy definition is invalid")


@dataclass
class _Reader:
    """Read typed fields from closed JSON values while collecting issues."""

    issues: list[ValidationIssue]

    def obj(self, value: JsonValue | None, path: str, required: bool = False) -> JsonValue:
        if value is None or value.kind is JsonKind.NULL:
            if required:
                self.issues.append(ValidationIssue(path, "missing_field"))
            return json_object(())
        if value.kind is not JsonKind.OBJECT:
            self.issues.append(ValidationIssue(path, "invalid_type"))
            return json_object(())
        return value

    def known(self, value: JsonValue, names: tuple[str, ...], path: str) -> None:
        for name in value.member_names():
            if name not in names:
                self.issues.append(ValidationIssue(f"{path}/{name}", "unknown_key"))

    def string(self, value: JsonValue | None, path: str, required: bool = False) -> str:
        text: str | None = value.as_str() if value is not None else None
        if text is None:
            if value is None and not required:
                return ""
            self.issues.append(
                ValidationIssue(path, "missing_field" if value is None else "invalid_type")
            )
            return ""
        return text

    def strings(self, value: JsonValue | None, path: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if value.kind is not JsonKind.ARRAY:
            self.issues.append(ValidationIssue(path, "invalid_type"))
            return ()
        result: list[str] = []
        for index, item in enumerate(value.items):
            text: str | None = item.as_str()
            if text is None:
                self.issues.append(ValidationIssue(f"{path}/{index}", "invalid_type"))
            else:
                result.append(text)
        return tuple(result)


def load_policy(text: str, source_format: SourceFormat) -> PolicyDefinition:
    """Parse a JSON or YAML placement policy and validate all closed fields."""
    try:
        document: JsonValue = (
            parse_yaml(text) if source_format is SourceFormat.YAML else parse_json(text)
        )
    except TelescreenError:
        raise PolicyValidationError((ValidationIssue("", "invalid_document"),)) from None
    reader: _Reader = _Reader([])
    root: JsonValue = reader.obj(document, "", True)
    reader.known(
        root,
        ("name", "effect", "match", "condition", "route_to_owner", "owner_team", "rationale"),
        "",
    )
    name: str = reader.string(root.get("name"), "/name", True)
    if name and _NAME.fullmatch(name) is None:
        reader.issues.append(ValidationIssue("/name", "invalid_value"))
    effect_text: str = reader.string(root.get("effect"), "/effect", True)
    effect: PolicyEffect = _enum(reader, effect_text, PolicyEffect, "/effect", PolicyEffect.VIOLATE)
    match: JsonValue = reader.obj(root.get("match"), "/match")
    reader.known(match, ("rule_classes", "sources", "locations"), "/match")
    rule_classes: tuple[RuleClass, ...] = tuple(
        _enum(reader, value, RuleClass, f"/match/rule_classes/{index}", RuleClass.CREDENTIAL)
        for index, value in enumerate(
            reader.strings(match.get("rule_classes"), "/match/rule_classes")
        )
    )
    sources: tuple[CollectorKind, ...] = tuple(
        _enum(reader, value, CollectorKind, f"/match/sources/{index}", CollectorKind.CUSTOM)
        for index, value in enumerate(reader.strings(match.get("sources"), "/match/sources"))
    )
    locations_value: JsonValue = reader.obj(match.get("locations"), "/match/locations")
    location_names: tuple[str, ...] = (
        "gdrive_drives",
        "gdrive_folders",
        "slack_channels",
        "github_repos",
    )
    reader.known(locations_value, location_names, "/match/locations")
    github_repos: tuple[str, ...] = tuple(
        value.lower()
        for index, value in enumerate(
            reader.strings(locations_value.get("github_repos"), "/match/locations/github_repos")
        )
        if _valid_github_selector(value, reader, index)
    )
    locations: PolicyLocations = PolicyLocations(
        reader.strings(locations_value.get("gdrive_drives"), "/match/locations/gdrive_drives"),
        reader.strings(locations_value.get("gdrive_folders"), "/match/locations/gdrive_folders"),
        reader.strings(locations_value.get("slack_channels"), "/match/locations/slack_channels"),
        github_repos,
    )
    condition_value: JsonValue | None = root.get("condition")
    condition: JsonValue = reader.obj(condition_value, "/condition")
    if condition_value is not None and condition_value.kind is not JsonKind.OBJECT:
        reader.issues.append(ValidationIssue("/condition", "invalid_type"))
    reader.known(condition, ("max_exposure", "max_age_days"), "/condition")
    if condition_value is not None and effect is not PolicyEffect.ALLOW:
        reader.issues.append(ValidationIssue("/condition", "allow_only"))
    exposure_text: str = reader.string(condition.get("max_exposure"), "/condition/max_exposure")
    max_exposure: Visibility | None = None
    if exposure_text:
        max_exposure = _enum(
            reader, exposure_text, Visibility, "/condition/max_exposure", Visibility.UNKNOWN
        )
        if max_exposure is Visibility.UNKNOWN:
            reader.issues.append(ValidationIssue("/condition/max_exposure", "invalid_value"))
    age_value: JsonValue | None = condition.get("max_age_days")
    max_age_days: int | None = age_value.as_int() if age_value is not None else None
    if age_value is not None and max_age_days is None:
        reader.issues.append(ValidationIssue("/condition/max_age_days", "invalid_type"))
    if max_age_days is not None and not 1 <= max_age_days <= 36500:
        reader.issues.append(ValidationIssue("/condition/max_age_days", "invalid_value"))
    route_value: JsonValue | None = root.get("route_to_owner")
    route_to_owner: bool = True
    if route_value is not None:
        route_result: bool | None = route_value.as_bool()
        if route_result is None:
            reader.issues.append(ValidationIssue("/route_to_owner", "invalid_type"))
        else:
            route_to_owner = route_result
    owner_team_value: JsonValue | None = root.get("owner_team")
    owner_team: str | None = None
    if owner_team_value is not None and not owner_team_value.is_null():
        owner_team = reader.string(owner_team_value, "/owner_team") or None
    rationale: str = reader.string(root.get("rationale"), "/rationale")
    if len(rationale) > 2000:
        reader.issues.append(ValidationIssue("/rationale", "too_long"))
    if reader.issues:
        raise PolicyValidationError(tuple(reader.issues))
    return PolicyDefinition(
        name,
        effect,
        rule_classes,
        sources,
        locations,
        max_exposure,
        max_age_days,
        route_to_owner,
        owner_team,
        rationale,
    )


def policy_to_json(policy: PolicyDefinition) -> JsonValue:
    """Serialize one policy definition into the closed compiled JSON model."""
    condition: tuple[tuple[str, JsonValue], ...] = tuple(
        (name, value)
        for name, value in (
            (
                "max_exposure",
                json_str(policy.max_exposure.value) if policy.max_exposure else json_null(),
            ),
            (
                "max_age_days",
                json_int(policy.max_age_days) if policy.max_age_days is not None else json_null(),
            ),
        )
        if value.kind is not JsonKind.NULL
    )
    members: list[tuple[str, JsonValue]] = [
        ("name", json_str(policy.name)),
        ("effect", json_str(policy.effect.value)),
        (
            "match",
            json_object(
                (
                    (
                        "rule_classes",
                        json_array(tuple(json_str(item.value) for item in policy.rule_classes)),
                    ),
                    ("sources", json_array(tuple(json_str(item.value) for item in policy.sources))),
                    (
                        "locations",
                        json_object(
                            tuple(
                                (name, json_array(tuple(json_str(item) for item in values)))
                                for name, values in (
                                    ("gdrive_drives", policy.locations.gdrive_drives),
                                    ("gdrive_folders", policy.locations.gdrive_folders),
                                    ("slack_channels", policy.locations.slack_channels),
                                    ("github_repos", policy.locations.github_repos),
                                )
                            )
                        ),
                    ),
                )
            ),
        ),
        ("route_to_owner", json_bool(policy.route_to_owner)),
        (
            "owner_team",
            json_str(policy.owner_team) if policy.owner_team is not None else json_null(),
        ),
        ("rationale", json_str(policy.rationale)),
    ]
    if condition:
        members.append(("condition", json_object(condition)))
    return json_object(tuple(members))


def policy_from_json(text: str) -> PolicyDefinition:
    """Decode compiled JSON through the same validator used for source documents."""
    try:
        document: JsonValue = parse_json(text)
    except TelescreenError:
        raise PolicyValidationError((ValidationIssue("", "invalid_compiled_policy"),)) from None
    from telescreen.core.json import dump_json

    return load_policy(dump_json(document), SourceFormat.JSON)


def _enum[T: str](reader: _Reader, value: str, enum_type: type[T], path: str, fallback: T) -> T:
    try:
        return enum_type(value)
    except ValueError:
        reader.issues.append(ValidationIssue(path, "invalid_value"))
        return fallback


def _valid_github_selector(value: str, reader: _Reader, index: int) -> bool:
    parts: tuple[str, ...] = tuple(value.split("/"))
    valid: bool = (
        len(parts) == 2
        and bool(parts[0])
        and bool(parts[1])
        and not any(character.isspace() for character in value)
    )
    if valid:
        repo: str = parts[1]
        valid = "*" not in parts[0] and (repo == "*" or "*" not in repo)
    if not valid:
        reader.issues.append(
            ValidationIssue(f"/match/locations/github_repos/{index}", "invalid_value")
        )
    return valid
