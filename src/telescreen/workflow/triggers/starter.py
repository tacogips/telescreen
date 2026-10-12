"""Start named active workflows for alert actions."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import parse_json
from telescreen.core.vocabulary import IssueEventKind, RunStatus, RunTrigger
from telescreen.db.models import WorkflowDefinitionRow, WorkflowRunRow
from telescreen.ports.workflow import RunStarter
from telescreen.workflow.dsl.loader import compiled_from_json
from telescreen.workflow.dsl.model import CompiledWorkflow
from telescreen.workflow.engine.repository import WorkflowRepository


class WorkflowRunEngine(Protocol):
    """Minimal workflow engine operation required by alert actions."""

    async def start_run(
        self,
        session: AsyncSession,
        definition_id: str,
        issue_id: str | None,
        trigger: RunTrigger,
    ) -> str:
        """Start a workflow in the caller's transaction."""
        ...


@dataclass
class WorkflowRunStarter(RunStarter):
    """Start an active workflow unless one of its runs is already active for the issue."""

    engine: WorkflowRunEngine
    repository: WorkflowRepository

    async def start_for_issue(
        self, session: AsyncSession, workflow_name: str, issue_id: str, trigger: RunTrigger
    ) -> str | None:
        """Start the active named workflow or return None when absent/already active."""
        definition: WorkflowDefinitionRow | None = await self.repository.definition_by_name(
            session, workflow_name
        )
        if definition is None:
            return None
        active_run: WorkflowRunRow | None = await session.scalar(
            select(WorkflowRunRow)
            .join(WorkflowDefinitionRow, WorkflowDefinitionRow.id == WorkflowRunRow.definition_id)
            .where(
                WorkflowDefinitionRow.name == workflow_name,
                WorkflowRunRow.issue_id == issue_id,
                WorkflowRunRow.status.in_((RunStatus.RUNNING.value, RunStatus.WAITING.value)),
            )
            .limit(1)
        )
        if active_run is not None:
            return None
        return await self.engine.start_run(session, definition.id, issue_id, trigger)

    async def trigger_events(
        self, session: AsyncSession, workflow_name: str
    ) -> tuple[IssueEventKind, ...] | None:
        """Return the active definition's event triggers, or None when it has none."""
        definition: WorkflowDefinitionRow | None = await self.repository.definition_by_name(
            session, workflow_name
        )
        if definition is None:
            return None
        workflow: CompiledWorkflow = compiled_from_json(parse_json(definition.compiled_json))
        return workflow.trigger.on if workflow.trigger is not None else None
