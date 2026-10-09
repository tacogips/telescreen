"""Workflow definition validation and immutable version management."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json
from telescreen.core.vocabulary import ActorKind, SourceFormat
from telescreen.db.models import WorkflowDefinitionRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.collectors import CollectorConfig, CollectorConfigReader
from telescreen.workflow.dsl.loader import (
    CompiledWorkflow,
    WorkflowValidationError,
    compiled_to_json,
    load_workflow,
)
from telescreen.workflow.dsl.model import MapStep, SubWorkflowStep
from telescreen.workflow.dsl.validate import ValidationIssue
from telescreen.workflow.engine.repository import WorkflowRepository


@dataclass
class WorkflowService:
    """Validate workflow source and manage immutable active versions."""

    redactor: Redactor
    configs: CollectorConfigReader
    audit: AuditLog
    clock: Clock
    repository: WorkflowRepository

    async def validate(self, source_format: SourceFormat, text: str) -> Sequence[ValidationIssue]:
        """Return safe DSL diagnostics, rejecting any source containing sensitive data."""
        if self.redactor.has_hits(text):
            return [ValidationIssue("", "raw_value_detected")]
        try:
            load_workflow(text, source_format)
        except WorkflowValidationError as error:
            return list(error.issues)
        return []

    async def upload(
        self,
        session: AsyncSession,
        source_format: SourceFormat,
        text: str,
        actor_ref: str,
    ) -> WorkflowDefinitionRow:
        """Validate and store a new inactive workflow version."""
        if self.redactor.has_hits(text):
            raise WorkflowValidationError((ValidationIssue("", "raw_value_detected"),))
        workflow: CompiledWorkflow = load_workflow(text, source_format)
        for step in workflow.steps:
            if isinstance(step, SubWorkflowStep):
                target: WorkflowDefinitionRow | None = await self.repository.definition_by_name(
                    session, step.workflow, step.version
                )
                if target is None:
                    raise WorkflowValidationError(
                        (ValidationIssue(f"/steps/{step.id}/workflow", "unknown_workflow"),)
                    )
            if isinstance(step, MapStep) and step.partition_via.value == "collector":
                config: CollectorConfig | None = await self.configs.get_by_name(
                    session, step.collector
                )
                if config is None:
                    raise WorkflowValidationError(
                        (ValidationIssue(f"/steps/{step.id}/collector", "unknown_collector"),)
                    )
        version_value: int | None = await session.scalar(
            select(func.max(WorkflowDefinitionRow.version)).where(
                WorkflowDefinitionRow.name == workflow.name
            )
        )
        version: int = (version_value or 0) + 1
        row: WorkflowDefinitionRow = WorkflowDefinitionRow(
            id=new_id(),
            name=workflow.name,
            version=version,
            source_format=source_format.value,
            source_text=text,
            compiled_json=dump_json(compiled_to_json(workflow)),
            active=False,
            created_by=actor_ref,
            created_at=self.clock.now(),
        )
        session.add(row)
        await session.flush()
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.SYSTEM,
                actor_ref,
                "workflow.uploaded",
                "workflow_definition",
                row.id,
                "Workflow version uploaded",
                compiled_to_json(workflow),
            ),
        )
        return row

    async def activate(
        self, session: AsyncSession, name: str, version: int
    ) -> WorkflowDefinitionRow:
        """Activate exactly one version for the selected workflow name."""
        target: WorkflowDefinitionRow | None = await session.scalar(
            select(WorkflowDefinitionRow).where(
                WorkflowDefinitionRow.name == name,
                WorkflowDefinitionRow.version == version,
            )
        )
        if target is None:
            from telescreen.core.errors import TelescreenError
            from telescreen.core.vocabulary import ErrorCode

            raise TelescreenError(ErrorCode.NOT_FOUND, "workflow version not found")
        rows: Sequence[WorkflowDefinitionRow] = tuple(
            (
                await session.scalars(
                    select(WorkflowDefinitionRow).where(WorkflowDefinitionRow.name == name)
                )
            ).all()
        )
        for row in rows:
            row.active = row.id == target.id
        await session.flush()
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:workflow",
                "workflow.activated",
                "workflow_definition",
                target.id,
                "Workflow version activated",
                compiled_to_json(
                    load_workflow(target.source_text, SourceFormat(target.source_format))
                ),
            ),
        )
        return target

    async def get(
        self, session: AsyncSession, name: str, version: int | None = None
    ) -> WorkflowDefinitionRow | None:
        """Read a named version or its active version."""
        return await self.repository.definition_by_name(session, name, version)

    async def list(self, session: AsyncSession) -> Sequence[WorkflowDefinitionRow]:
        """List active workflow definitions."""
        return await self.repository.definitions(session)

    async def versions(self, session: AsyncSession, name: str) -> Sequence[WorkflowDefinitionRow]:
        """List all versions of one workflow by ascending version."""
        return tuple(
            (
                await session.scalars(
                    select(WorkflowDefinitionRow)
                    .where(WorkflowDefinitionRow.name == name)
                    .order_by(WorkflowDefinitionRow.version)
                )
            ).all()
        )
