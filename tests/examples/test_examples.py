"""Validate deployment and workflow examples without starting external services."""

from pathlib import Path

from telescreen.core.json import JsonKind, JsonValue, dump_json, parse_json, parse_yaml
from telescreen.core.vocabulary import IssueEventKind, SourceFormat, StepType
from telescreen.masking.redact import Redactor
from telescreen.workflow.dsl.escalation import EscalationPolicySpec, load_escalation_policy
from telescreen.workflow.dsl.loader import load_workflow
from telescreen.workflow.dsl.model import CompiledWorkflow
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
    assert len(paths) == 4
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
    assert names == {"secops-standard", "dev-remediation"}


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


def test_env_example_uses_secret_references_or_placeholders() -> None:
    """Require secret-looking variables to be references or obvious placeholders."""
    lines: list[str] = (_COMPOSE / ".env.example").read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped: str = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name: str
        value: str
        name, value = stripped.split("=", maxsplit=1)
        if name.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            normalized: str = value.lower()
            assert value.startswith(("env:", "file:")) or "placeholder" in normalized, name
