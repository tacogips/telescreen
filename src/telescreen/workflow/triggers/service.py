"""Issue event subscription to active workflow definitions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import parse_json
from telescreen.core.vocabulary import RunStatus, RunTrigger, Severity
from telescreen.db.models import WorkflowDefinitionRow, WorkflowRunRow
from telescreen.ports.issues import IssueEvent, IssueEventSink
from telescreen.workflow.dsl.loader import compiled_from_json
from telescreen.workflow.dsl.model import CompiledWorkflow, TriggerMatch, TriggerSpec
from telescreen.workflow.engine.engine import RunEngine
from telescreen.workflow.engine.repository import WorkflowRepository


@dataclass
class WorkflowTriggerService(IssueEventSink):
    """Start matching active workflows once per issue while a run is active."""

    engine: RunEngine
    repository: WorkflowRepository

    async def on_issue_event(self, session: AsyncSession, event: IssueEvent) -> None:
        """Start active definitions whose event and filters match the issue."""
        definitions: Sequence[WorkflowDefinitionRow] = await self.repository.definitions(session)
        for definition in definitions:
            workflow: CompiledWorkflow = compiled_from_json(parse_json(definition.compiled_json))
            trigger: TriggerSpec | None = workflow.trigger
            if trigger is None or event.kind not in trigger.on:
                continue
            if not _matches(trigger.match, event):
                continue
            active_run: WorkflowRunRow | None = await session.scalar(
                select(WorkflowRunRow)
                .join(
                    WorkflowDefinitionRow,
                    WorkflowDefinitionRow.id == WorkflowRunRow.definition_id,
                )
                .where(
                    WorkflowDefinitionRow.name == definition.name,
                    WorkflowRunRow.issue_id == event.issue_id,
                    WorkflowRunRow.status.in_((RunStatus.RUNNING.value, RunStatus.WAITING.value)),
                )
                .limit(1)
            )
            if active_run is not None:
                continue
            await self.engine.start_run(
                session,
                definition.id,
                event.issue_id,
                RunTrigger(event.kind.value),
            )


def _matches(match: TriggerMatch, event: IssueEvent) -> bool:
    """Apply optional source, rule class, and minimum-severity filters."""
    if match.sources and event.source not in match.sources:
        return False
    if match.rule_classes and event.rule_class not in match.rule_classes:
        return False
    if match.min_severity is not None:
        severity_order: tuple[Severity, ...] = (Severity.MEDIUM, Severity.HIGH)
        if severity_order.index(event.severity) < severity_order.index(match.min_severity):
            return False
    return True
