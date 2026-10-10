"""Validate deployment and workflow examples without starting external services."""

from datetime import timedelta
from pathlib import Path

import pytest
from pytest import CaptureFixture

from telescreen.cli import main
from telescreen.core.json import JsonKind, JsonValue, dump_json, parse_json, parse_yaml
from telescreen.core.vocabulary import (
    HumanTaskKind,
    IssueEventKind,
    Severity,
    SourceFormat,
    StepType,
)
from telescreen.masking.redact import Redactor
from telescreen.workflow.dsl.escalation import EscalationPolicySpec, load_escalation_policy
from telescreen.workflow.dsl.loader import load_workflow
from telescreen.workflow.dsl.model import CompiledWorkflow, HumanTaskStep
from tests.support.samples import find_leaks

_ROOT: Path = Path(__file__).resolve().parents[2]
_COMPOSE: Path = _ROOT / "examples" / "compose"
_WORKFLOWS: Path = _ROOT / "examples" / "workflows"


def _workflow_paths() -> tuple[Path, ...]:
    """Return the workflow YAML files in a stable order."""
    return tuple(sorted(_WORKFLOWS.glob("*.yaml")))


def _example_files() -> tuple[Path, ...]:
    """Return all authored compose and workflow example files."""
    return tuple(sorted(path for root in (_COMPOSE, _WORKFLOWS) for path in root.rglob("*")))


def test_all_workflows_load_and_contain_no_detected_sensitive_values() -> None:
    """Load every workflow through the DSL and reject samples or redactor hits."""
    redactor: Redactor = Redactor.default()
    paths: tuple[Path, ...] = _workflow_paths()
    assert len(paths) == 5
    for path in paths:
        text: str = path.read_text(encoding="utf-8")
        load_workflow(text, SourceFormat.YAML)
        assert not redactor.has_hits(text), path.name
        assert not find_leaks(text), path.name


def test_escalation_policy_file_loads_both_policies() -> None:
    """Parse and validate each escalation policy in the JSON collection."""
    text: str = (_WORKFLOWS / "escalation-policies.json").read_text(encoding="utf-8")
    policies: JsonValue = parse_json(text)
    values: tuple[JsonValue, ...] | None = policies.as_list()
    assert values is not None
    assert len(values) == 2
    names: set[str] = set()
    for value in values:
        assert value.kind is JsonKind.OBJECT
        policy: EscalationPolicySpec = load_escalation_policy(dump_json(value), SourceFormat.JSON)
        names.add(policy.name)
        if policy.name == "secops-standard":
            assert policy.tiers[0].min_risk == 60
    assert names == {"secops-standard", "dev-remediation"}


def test_owner_review_workflow_has_required_routes_and_escalation() -> None:
    """Keep owner-review routing, SLA, and escalation policy explicit."""
    text: str = (_WORKFLOWS / "owner-review.yaml").read_text(encoding="utf-8")
    workflow: CompiledWorkflow = load_workflow(text, SourceFormat.YAML)
    assert workflow.trigger is not None
    assert workflow.trigger.on == (IssueEventKind.ISSUE_CREATED,)
    assert workflow.trigger.match.min_severity is Severity.HIGH
    owner_tasks: tuple[HumanTaskStep, ...] = tuple(
        step for step in workflow.steps if isinstance(step, HumanTaskStep)
    )
    assert len(owner_tasks) == 1
    owner_task: HumanTaskStep = owner_tasks[0]
    assert owner_task.kind is HumanTaskKind.OWNER_REVIEW
    assert owner_task.assignee == "issue:assignee"
    assert {route.name for route in owner_task.outcomes} == {
        "remove",
        "restrict",
        "exception",
        "not_mine",
    }
    assert owner_task.sla == timedelta(days=2)
    assert owner_task.escalation_policy == "secops-standard"


def test_github_workflow_contains_required_step_types() -> None:
    """Keep the multi-team remediation path represented in the example."""
    text: str = (_WORKFLOWS / "github-credential-leak.yaml").read_text(encoding="utf-8")
    workflow: CompiledWorkflow = load_workflow(text, SourceFormat.YAML)
    assert workflow.trigger is not None
    assert workflow.trigger.on == (
        IssueEventKind.ISSUE_CREATED,
        IssueEventKind.ISSUE_REGRESSED,
    )
    step_types: set[StepType] = {step.step_type for step in workflow.steps}
    assert step_types == {
        StepType.DETECT,
        StepType.AI_AGENT,
        StepType.CONDITION,
        StepType.HUMAN_TASK,
        StepType.NOTIFY,
        StepType.EVIDENCE_UPLOAD,
        StepType.POLICE_VERIFY,
        StepType.SEND_BACK,
        StepType.END,
    }


def test_compose_stack_parses_with_six_services() -> None:
    """Parse the compose file and verify all six deployment processes exist."""
    text: str = (_COMPOSE / "docker-compose.yml").read_text(encoding="utf-8")
    compose: JsonValue = parse_yaml(text)
    services: JsonValue | None = compose.get("services")
    assert services is not None
    assert services.kind is JsonKind.OBJECT
    assert set(services.member_names()) == {
        "postgres",
        "meilisearch",
        "migrate",
        "bureau",
        "engine",
        "example-police",
    }


def test_example_files_contain_no_sample_secrets() -> None:
    """Scan every file under the compose and workflows directories for samples."""
    for path in _example_files():
        if path.is_file():
            text: str = path.read_text(encoding="utf-8")
            assert not find_leaks(text), path.name


def test_env_example_contains_no_detected_sensitive_values() -> None:
    """Scan the environment template with the existing sample and redactor checks."""
    text: str = (_COMPOSE / ".env.example").read_text(encoding="utf-8")
    assert not Redactor.default().has_hits(text)
    assert not find_leaks(text)


def test_env_example_uses_secret_references_or_placeholders() -> None:
    """Require secret-looking variables to be references or obvious placeholders."""
    lines: list[str] = (_COMPOSE / ".env.example").read_text(encoding="utf-8").splitlines()
    active_names: set[str] = set()
    for line in lines:
        stripped: str = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name: str
        value: str
        name, value = stripped.split("=", maxsplit=1)
        active_names.add(name)
        if name.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            normalized: str = value.lower()
            assert (
                value.startswith(("env:", "file:"))
                or "placeholder" in normalized
                or normalized == "changeme"
            ), name
    assert "TELESCREEN_SLACK_SIGNING_SECRET" not in active_names
    assert "TELESCREEN_GITHUB_WEBHOOK_SECRET" not in active_names


def test_compose_sets_ui_dist_and_example_police_fingerprint_key() -> None:
    """Configure the served UI and give the example Police its own key value."""
    compose: JsonValue = parse_yaml((_COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    services: JsonValue | None = compose.get("services")
    assert services is not None
    bureau: JsonValue | None = services.get("bureau")
    police: JsonValue | None = services.get("example-police")
    assert bureau is not None
    assert police is not None
    bureau_environment: JsonValue | None = bureau.get("environment")
    police_environment: JsonValue | None = police.get("environment")
    assert bureau_environment is not None
    assert police_environment is not None
    web_dist: JsonValue | None = bureau_environment.get("TELESCREEN_WEB_DIST")
    fingerprint_key: JsonValue | None = police_environment.get("TELESCREEN_FINGERPRINT_KEY")
    assert web_dist is not None and web_dist.as_str() == "/app/web"
    assert fingerprint_key is not None
    assert fingerprint_key.as_str() == "${POLICE_FINGERPRINT_KEY}"


def test_compose_mounts_named_evidence_volume_for_bureau_and_engine() -> None:
    """Persist accepted evidence on the Bureau and worker services."""
    compose: JsonValue = parse_yaml((_COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    services: JsonValue | None = compose.get("services")
    volumes: JsonValue | None = compose.get("volumes")
    assert services is not None
    assert volumes is not None
    assert "evidence-data" in volumes.member_names()
    for service_name in ("bureau", "engine"):
        service: JsonValue | None = services.get(service_name)
        assert service is not None
        service_volumes: JsonValue | None = service.get("volumes")
        assert service_volumes is not None
        entries: tuple[JsonValue, ...] | None = service_volumes.as_list()
        assert entries is not None
        assert any((entry.as_str() or "").endswith(":/app/var/evidence") for entry in entries)


def test_compose_dockerfile_builds_and_serves_the_web_assets() -> None:
    """Keep the container build frozen and copy its UI to the served path."""
    dockerfile: str = (_COMPOSE / "Dockerfile").read_text(encoding="utf-8")
    assert "RUN bun install --frozen-lockfile" in dockerfile
    assert "COPY --from=web-build /app/src/web/dist/ /app/web/" in dockerfile


def test_dockerfile_chowns_evidence_directory_before_non_root_user() -> None:
    """Create writable evidence storage after user creation and before switching users."""
    lines: list[str] = (_COMPOSE / "Dockerfile").read_text(encoding="utf-8").splitlines()
    useradd_line: int = next(index for index, line in enumerate(lines) if "useradd" in line)
    evidence_line: int = next(
        index for index, line in enumerate(lines) if "mkdir -p /app/var/evidence" in line
    )
    chown_line: int = next(
        index
        for index, line in enumerate(lines)
        if "chown -R telescreen:telescreen /app/var" in line
    )
    user_line: int = next(
        index for index, line in enumerate(lines) if line.strip() == "USER telescreen"
    )
    assert useradd_line < evidence_line < chown_line < user_line


def test_compose_does_not_enable_ui_mock_mode() -> None:
    """Keep mock fixtures out of the deployment configuration."""
    for path in _COMPOSE.rglob("*"):
        if path.is_file():
            assert "VITE_TELESCREEN_MOCK" not in path.read_text(encoding="utf-8"), path.name


def test_compose_cli_subcommands_exist(capsys: CaptureFixture[str]) -> None:
    """Require every telescreen subcommand used by Compose in the CLI help."""
    compose: JsonValue = parse_yaml((_COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    services: JsonValue | None = compose.get("services")
    assert services is not None
    compose_subcommands: set[str] = set()
    for service_name in services.member_names():
        service: JsonValue | None = services.get(service_name)
        assert service is not None
        command: JsonValue | None = service.get("command")
        if command is None:
            continue
        arguments: tuple[JsonValue, ...] | None = command.as_list()
        assert arguments is not None
        if arguments[0].as_str() == "telescreen":
            subcommand: str | None = arguments[1].as_str() if len(arguments) > 1 else None
            assert subcommand is not None
            compose_subcommands.add(subcommand)
    with pytest.raises(SystemExit):
        main(["--help"])
    help_text: str = capsys.readouterr().out
    for subcommand in compose_subcommands:
        assert subcommand in help_text


def test_active_compose_file_secrets_are_declared() -> None:
    """Require active env file references to have Compose secret declarations."""
    compose: JsonValue = parse_yaml((_COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    secrets: JsonValue | None = compose.get("secrets")
    assert secrets is not None
    declared_names: set[str] = set(secrets.member_names())
    lines: list[str] = (_COMPOSE / ".env.example").read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped: str = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        value: str = stripped.split("=", maxsplit=1)[1]
        if value.startswith("file:/run/secrets/"):
            secret_name: str = value.rsplit("/", maxsplit=1)[-1]
            assert secret_name in declared_names


def test_workflow_examples_validate_through_the_cli() -> None:
    """Run the documented offline workflow validation command for all examples."""
    paths: tuple[Path, ...] = _workflow_paths()
    assert {path.name for path in paths} == {
        "github-credential-leak.yaml",
        "slack-pii.yaml",
        "gdrive-mynumber-sheet.yaml",
        "drive-weekly-sweep.yaml",
        "owner-review.yaml",
    }
    for path in paths:
        assert main(["workflow", "validate", str(path)]) == 0
