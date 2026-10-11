"""Static workflow graph and reference validation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from telescreen.workflow.dsl.model import (
    AiAgentStep,
    CompiledWorkflow,
    ConditionStep,
    DetectStep,
    EndStep,
    EvidenceUploadStep,
    Expression,
    HumanTaskStep,
    MapStep,
    Outcomes3,
    ParallelStep,
    PoliceVerifyStep,
    SendBackStep,
    StepSpec,
    SubWorkflowStep,
    Template,
)


@dataclass(frozen=True)
class ValidationIssue:
    """Safe validation location and closed error code."""

    path: str
    code: str


_SLUG: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RECIPIENT: re.Pattern[str] = re.compile(r"^(member|team|email|slack):.+$")


def validate_workflow(workflow: CompiledWorkflow) -> list[ValidationIssue]:
    """Return all graph, typed-model and notification-set validation issues."""
    from telescreen.workflow.dsl.notification_sets import notification_set_issues

    issues: list[ValidationIssue] = notification_set_issues(workflow)
    by_id: list[tuple[str, StepSpec]] = []
    positions: list[tuple[str, int]] = []
    for index, step in enumerate(workflow.steps):
        path: str = f"/steps/{index}"
        if _SLUG.fullmatch(step.id) is None:
            issues.append(ValidationIssue(f"{path}/id", "invalid_value"))
        if any(existing_id == step.id for existing_id, _ in by_id):
            issues.append(ValidationIssue(f"{path}/id", "duplicate_id"))
        by_id.append((step.id, step))
        positions.append((step.id, index))
        _validate_step(step, path, issues)
    if _step_by_id(by_id, workflow.start) is None:
        issues.append(ValidationIssue("/start", "unknown_step"))
        return issues
    edges: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
        (
            step_id,
            tuple(target for target, _ in _target_paths(step, _position(positions, step_id))),
        )
        for step_id, step in by_id
    )
    for step_id, step in by_id:
        for target, target_path in _target_paths(step, _position(positions, step_id)):
            if _step_by_id(by_id, target) is None:
                issues.append(ValidationIssue(target_path, "unknown_step"))
    reached: set[str] = _reachable(workflow.start, edges)
    for step_id, index in positions:
        if step_id not in reached:
            issues.append(ValidationIssue(f"/steps/{index}/id", "unreachable_step"))
    for step in workflow.steps:
        if (
            isinstance(step, SendBackStep)
            and _step_by_id(by_id, step.to) is not None
            and step.id not in _reachable(step.to, edges)
        ):
            issues.append(
                ValidationIssue(f"/steps/{_position(positions, step.id)}/to", "invalid_send_back")
            )
        if isinstance(step, ParallelStep):
            for branch in step.branches:
                if (
                    _step_by_id(by_id, branch) is None
                    or step.join not in _reachable(branch, edges)
                    or _reaches_end_without_join(branch, step.join, by_id, edges)
                ):
                    issues.append(
                        ValidationIssue(
                            f"/steps/{_position(positions, step.id)}/branches", "invalid_parallel"
                        )
                    )
    return issues


def _validate_step(step: StepSpec, path: str, issues: list[ValidationIssue]) -> None:
    if step.on_error is not None:
        pass
    if isinstance(step, HumanTaskStep):
        if not step.outcomes:
            issues.append(ValidationIssue(f"{path}/outcomes", "empty_outcomes"))
        if step.kind.value == "owner_review":
            names: frozenset[str] = frozenset(route.name for route in step.outcomes)
            if len(step.outcomes) != 4 or names != frozenset(
                ("remove", "restrict", "exception", "not_mine")
            ):
                issues.append(ValidationIssue(f"{path}/outcomes", "owner_review_outcomes"))
        if step.assignee != "issue:assignee":
            _recipient(step.assignee, f"{path}/assignee", issues)
    if isinstance(step, EvidenceUploadStep):
        _recipient(step.assignee, f"{path}/assignee", issues)
    if isinstance(step, SendBackStep) and step.max_send_backs < 1:
        issues.append(ValidationIssue(f"{path}/max_send_backs", "invalid_value"))
    if isinstance(step, EndStep) and step.result.value not in ("succeeded", "failed", "cancelled"):
        issues.append(ValidationIssue(f"{path}/result", "invalid_value"))
    if isinstance(step, MapStep):
        if step.map_via is not step.partition_via:
            issues.append(ValidationIssue(f"{path}/map/via", "invalid_value"))
        if step.partition_via.value == "police" and (
            not step.partition_task_type or not step.map_task_type
        ):
            issues.append(ValidationIssue(f"{path}/source/partition/task_type", "missing_field"))
        if (
            step.max_concurrency < 1
            or step.shard_max_attempts < 1
            or not 0 <= step.max_failed_ratio <= 1
            or step.max_failed_items < 0
        ):
            if step.max_concurrency < 1:
                issues.append(ValidationIssue(f"{path}/max_concurrency", "invalid_value"))
            if step.shard_max_attempts < 1:
                issues.append(ValidationIssue(f"{path}/shard_max_attempts", "invalid_value"))
            if not 0 <= step.max_failed_ratio <= 1:
                issues.append(ValidationIssue(f"{path}/failure/max_failed_ratio", "invalid_value"))
            if step.max_failed_items < 0:
                issues.append(ValidationIssue(f"{path}/failure/max_failed_items", "invalid_value"))
    if isinstance(step, AiAgentStep):
        _validate_template_paths(step.prompt, f"{path}/prompt", issues)
    if isinstance(step, ConditionStep):
        for case_index, case in enumerate(step.cases):
            for expression_path in _expression_paths(case.when):
                if not _known_path(expression_path):
                    issues.append(
                        ValidationIssue(f"{path}/cases/{case_index}/when", "invalid_expression")
                    )
    if isinstance(step, (HumanTaskStep, EvidenceUploadStep)):
        _validate_template_paths(step.title, f"{path}/title", issues)
    if isinstance(step, PoliceVerifyStep):
        for item in step.input:
            _validate_template_paths(item.template, f"{path}/input/{item.name}", issues)
    if isinstance(step, SendBackStep):
        _validate_template_paths(step.reason, f"{path}/reason", issues)


def _recipient(value: str, path: str, issues: list[ValidationIssue]) -> None:
    if _RECIPIENT.fullmatch(value) is None:
        issues.append(ValidationIssue(path, "invalid_value"))


def _target_paths(step: StepSpec, index: int) -> tuple[tuple[str, str], ...]:
    path: str = f"/steps/{index}"
    targets: list[tuple[str, str]] = []
    if step.next:
        targets.append((step.next, f"{path}/next"))
    if step.on_error:
        targets.append((step.on_error, f"{path}/on_error"))
    if isinstance(step, (DetectStep, PoliceVerifyStep, SubWorkflowStep, MapStep)):
        outcomes: Outcomes3 = step.outcomes
        routed: tuple[tuple[str, str | None], ...] = (
            ("found", outcomes.found),
            ("clean", outcomes.clean),
            ("error", outcomes.error),
            ("passed", outcomes.passed),
            ("failed", outcomes.failed),
            ("succeeded", outcomes.succeeded),
            ("partial", outcomes.partial),
        )
        for name, target in routed:
            if target:
                targets.append((target, f"{path}/outcomes/{name}"))
    if isinstance(step, ConditionStep):
        targets.extend(
            (case.next, f"{path}/cases/{case_index}/next")
            for case_index, case in enumerate(step.cases)
        )
        targets.append((step.default, f"{path}/default"))
    if isinstance(step, HumanTaskStep):
        targets.extend((route.to, f"{path}/outcomes/{route.name}") for route in step.outcomes)
    if isinstance(step, SendBackStep):
        targets.append((step.to, f"{path}/to"))
        if step.on_exhausted:
            targets.append((step.on_exhausted, f"{path}/on_exhausted"))
    if isinstance(step, ParallelStep):
        targets.extend(
            (branch, f"{path}/branches/{branch_index}")
            for branch_index, branch in enumerate(step.branches)
        )
        targets.append((step.join, f"{path}/join"))
    return tuple(targets)


def _reachable(start: str, edges: tuple[tuple[str, tuple[str, ...]], ...]) -> set[str]:
    visited: set[str] = set()
    pending: list[str] = [start]
    while pending:
        current: str = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(target for target in _targets_by_id(edges, current) if target not in visited)
    return visited


def _reaches_end_without_join(
    start: str,
    join: str,
    steps: list[tuple[str, StepSpec]],
    edges: tuple[tuple[str, tuple[str, ...]], ...],
) -> bool:
    pending: list[str] = [start]
    seen: set[str] = set()
    while pending:
        current: str = pending.pop()
        if current == join or current in seen:
            continue
        seen.add(current)
        if isinstance(_step_by_id(steps, current), EndStep):
            return True
        pending.extend(_targets_by_id(edges, current))
    return False


def _expression_paths(expression: Expression) -> tuple[tuple[str, ...], ...]:
    paths: list[tuple[str, ...]] = [expression.path] if expression.kind == "path" else []
    for child in expression.children:
        paths.extend(_expression_paths(child))
    return tuple(paths)


def _known_path(path: tuple[str, ...]) -> bool:
    if len(path) >= 2 and path[0] in ("issue", "run"):
        return True
    return len(path) >= 3 and path[0] == "steps" and (path[2] == "outcome" or path[2] == "output")


def _validate_template_paths(template: Template, path: str, issues: list[ValidationIssue]) -> None:
    for part in template:
        if part.path and not _known_path(part.path):
            issues.append(ValidationIssue(path, "invalid_template"))


def _step_by_id(steps: list[tuple[str, StepSpec]], step_id: str) -> StepSpec | None:
    return next((step for key, step in steps if key == step_id), None)


def _targets_by_id(edges: tuple[tuple[str, tuple[str, ...]], ...], step_id: str) -> tuple[str, ...]:
    return next((targets for key, targets in edges if key == step_id), ())


def _position(positions: list[tuple[str, int]], step_id: str) -> int:
    return next((index for key, index in positions if key == step_id), 0)
