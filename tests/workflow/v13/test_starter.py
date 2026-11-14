"""RunStarter adapter tests for alert-triggered workflow actions."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import dump_json
from telescreen.core.vocabulary import IssueEventKind, RunStatus, RunTrigger, SourceFormat
from telescreen.db.models import WorkflowDefinitionRow, WorkflowRunRow
from telescreen.workflow.dsl.compiled import compiled_to_json
from telescreen.workflow.dsl.loader import load_workflow
from telescreen.workflow.dsl.model import CompiledWorkflow
from telescreen.workflow.engine.repository import WorkflowRepository
from telescreen.workflow.triggers.starter import WorkflowRunStarter
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class FakeEngine:
    """Capture workflow starts requested by the starter adapter."""

    calls: list[tuple[str, str | None, RunTrigger]] = field(default_factory=list)

    async def start_run(
        self,
        session: AsyncSession,
        definition_id: str,
        issue_id: str | None,
        trigger: RunTrigger,
    ) -> str:
        """Record the workflow start and return its stable test id."""
        del session
        self.calls.append((definition_id, issue_id, trigger))
        return "run-new"


async def test_workflow_starter_resolves_active_trigger_and_avoids_duplicate_run() -> None:
    """Trigger lookup reads the active definition and same-issue active runs block starts."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    engine: FakeEngine = FakeEngine()
    starter: WorkflowRunStarter = WorkflowRunStarter(engine, WorkflowRepository())
    workflow_text: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: triage
trigger:
  "on": [issue_created]
  match: {}
start: done
steps:
  - id: done
    type: end
"""
    workflow: CompiledWorkflow = load_workflow(workflow_text, SourceFormat.YAML)
    definition: WorkflowDefinitionRow = WorkflowDefinitionRow(
        id="definition-1",
        name="triage",
        version=1,
        source_format=SourceFormat.YAML.value,
        source_text=workflow_text,
        compiled_json=dump_json(compiled_to_json(workflow)),
        active=True,
        created_by="member:admin",
        created_at=now,
    )
    async with harness.sessions.begin() as session:
        session.add(definition)
    async with harness.sessions() as session:
        assert await starter.trigger_events(session, "missing") is None
        assert await starter.trigger_events(session, "triage") == (IssueEventKind.ISSUE_CREATED,)
    async with harness.sessions.begin() as session:
        started: str | None = await starter.start_for_issue(
            session, "triage", "issue-1", RunTrigger.ALERT_RULE
        )
    assert started == "run-new"
    assert engine.calls == [("definition-1", "issue-1", RunTrigger.ALERT_RULE)]
    async with harness.sessions.begin() as session:
        session.add(
            WorkflowRunRow(
                id="run-active",
                definition_id="definition-1",
                issue_id="issue-1",
                parent_step_run_id=None,
                status=RunStatus.RUNNING.value,
                trigger=RunTrigger.ALERT_RULE.value,
                context_json="{}",
                lock_version=0,
                started_at=now,
                finished_at=None,
            )
        )
    async with harness.sessions.begin() as session:
        duplicate: str | None = await starter.start_for_issue(
            session, "triage", "issue-1", RunTrigger.ALERT_RULE
        )
    assert duplicate is None
    assert len(engine.calls) == 1
    await harness.close()
