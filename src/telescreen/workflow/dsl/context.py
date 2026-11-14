"""Masked run context exposed to workflow expressions and templates."""

from __future__ import annotations

from dataclasses import dataclass

from telescreen.core.json import JsonKind, JsonValue, json_int, json_null, json_object, json_str


@dataclass(frozen=True)
class IssueContext:
    """Safe issue fields available to a workflow run."""

    number: int
    short_id: str
    title: str
    status: str
    source: str
    rule_class: str
    severity: str
    display_locator: str
    times_seen: int
    first_seen: str
    last_seen: str


@dataclass(frozen=True)
class RunInfo:
    """Workflow run identity and start time."""

    id: str
    workflow: str
    started_at: str


@dataclass(frozen=True)
class StepResultContext:
    """Latest safe result from one visited workflow step."""

    step_id: str
    outcome: str | None
    output: JsonValue


@dataclass(frozen=True)
class RunContext:
    """Masked issue, run and step data available to DSL expressions."""

    issue: IssueContext | None
    run: RunInfo
    steps: tuple[StepResultContext, ...] = ()

    def lookup(self, path: tuple[str, ...]) -> JsonValue | None:
        """Resolve a known issue, run or latest-step path."""
        if not path:
            return None
        if path[0] == "issue" and self.issue is not None:
            return _lookup_object(_issue_json(self.issue), path[1:])
        if path[0] == "run":
            return _lookup_object(
                json_object(
                    (
                        ("id", json_str(self.run.id)),
                        ("workflow", json_str(self.run.workflow)),
                        ("started_at", json_str(self.run.started_at)),
                    )
                ),
                path[1:],
            )
        if path[0] == "steps" and len(path) >= 3:
            result: StepResultContext | None = next(
                (item for item in self.steps if item.step_id == path[1]), None
            )
            if result is None:
                return None
            if path[2] == "outcome":
                return json_str(result.outcome) if result.outcome is not None else json_null()
            if path[2] == "output":
                return _lookup_object(result.output, path[3:])
        return None

    def with_step(self, result: StepResultContext) -> RunContext:
        """Replace the latest result for a step while preserving other visits."""
        retained: tuple[StepResultContext, ...] = tuple(
            item for item in self.steps if item.step_id != result.step_id
        )
        return RunContext(self.issue, self.run, retained + (result,))

    def to_json(self) -> JsonValue:
        """Serialize safe workflow context for database storage."""
        return json_object(
            (
                ("issue", _issue_json(self.issue) if self.issue is not None else json_null()),
                (
                    "run",
                    json_object(
                        (
                            ("id", json_str(self.run.id)),
                            ("workflow", json_str(self.run.workflow)),
                            ("started_at", json_str(self.run.started_at)),
                        )
                    ),
                ),
                (
                    "steps",
                    JsonValue(JsonKind.ARRAY, items=tuple(_step_json(item) for item in self.steps)),
                ),
            )
        )

    @staticmethod
    def from_json(value: JsonValue) -> RunContext:
        """Restore safe context from its closed JSON representation."""
        run_value: JsonValue = _required(value.get("run"))
        run: RunInfo = RunInfo(
            _required_string(run_value.get("id")),
            _required_string(run_value.get("workflow")),
            _required_string(run_value.get("started_at")),
        )
        issue_value: JsonValue | None = value.get("issue")
        issue: IssueContext | None = (
            _issue_from_json(issue_value)
            if issue_value is not None and issue_value.kind is JsonKind.OBJECT
            else None
        )
        steps_value: JsonValue = _required(value.get("steps"))
        steps: tuple[StepResultContext, ...] = tuple(
            _step_from_json(item) for item in steps_value.items
        )
        return RunContext(issue, run, steps)


def _issue_json(issue: IssueContext | None) -> JsonValue:
    if issue is None:
        return json_null()
    return json_object(
        (
            ("number", json_int(issue.number)),
            ("short_id", json_str(issue.short_id)),
            ("title", json_str(issue.title)),
            ("status", json_str(issue.status)),
            ("source", json_str(issue.source)),
            ("rule_class", json_str(issue.rule_class)),
            ("severity", json_str(issue.severity)),
            ("display_locator", json_str(issue.display_locator)),
            ("times_seen", json_int(issue.times_seen)),
            ("first_seen", json_str(issue.first_seen)),
            ("last_seen", json_str(issue.last_seen)),
        )
    )


def _step_json(step: StepResultContext) -> JsonValue:
    return json_object(
        (
            ("step_id", json_str(step.step_id)),
            ("outcome", json_str(step.outcome) if step.outcome is not None else json_null()),
            ("output", step.output),
        )
    )


def _lookup_object(value: JsonValue, path: tuple[str, ...]) -> JsonValue | None:
    current: JsonValue = value
    for part in path:
        found: JsonValue | None = current.get(part)
        if found is None:
            return None
        current = found
    return current


def _required(value: JsonValue | None) -> JsonValue:
    if value is None:
        raise ValueError("invalid run context")
    return value


def _required_string(value: JsonValue | None) -> str:
    text: str | None = value.as_str() if value is not None else None
    if text is None:
        raise ValueError("invalid run context")
    return text


def _issue_from_json(value: JsonValue) -> IssueContext:
    return IssueContext(
        _required_number(value.get("number")),
        _required_string(value.get("short_id")),
        _required_string(value.get("title")),
        _required_string(value.get("status")),
        _required_string(value.get("source")),
        _required_string(value.get("rule_class")),
        _required_string(value.get("severity")),
        _required_string(value.get("display_locator")),
        _required_number(value.get("times_seen")),
        _required_string(value.get("first_seen")),
        _required_string(value.get("last_seen")),
    )


def _step_from_json(value: JsonValue) -> StepResultContext:
    outcome_value: JsonValue = _required(value.get("outcome"))
    return StepResultContext(
        _required_string(value.get("step_id")),
        outcome_value.as_str(),
        _required(value.get("output")),
    )


def _required_number(value: JsonValue | None) -> int:
    number: int | None = value.as_int() if value is not None else None
    if number is None:
        raise ValueError("invalid run context")
    return number
