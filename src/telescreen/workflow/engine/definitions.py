"""Workflow definition validation and immutable version management."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json, json_int, json_object, json_str, parse_json
from telescreen.core.vocabulary import ActorKind, CollectorKind, NotificationEvent, SourceFormat
from telescreen.db.models import EscalationPolicyRow, WorkflowDefinitionRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.collectors import CollectorConfig, CollectorConfigReader
from telescreen.ports.templates import TemplateCatalog, TemplateInfo
from telescreen.workflow.dsl.escalation import EscalationPolicySpec, escalation_from_json
from telescreen.workflow.dsl.loader import (
    CompiledWorkflow,
    WorkflowValidationError,
    compiled_to_json,
    load_workflow,
)
from telescreen.workflow.dsl.model import (
    EvidenceUploadStep,
    HumanTaskStep,
    MapStep,
    NotificationTargetSpec,
    NotifyStep,
    SubWorkflowStep,
)
from telescreen.workflow.dsl.raw_scan import source_has_raw_values
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
    templates: TemplateCatalog | None = None

    async def validate(self, source_format: SourceFormat, text: str) -> Sequence[ValidationIssue]:
        """Return safe DSL diagnostics, rejecting any source containing sensitive data."""
        if source_has_raw_values(self.redactor, text):
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
        if source_has_raw_values(self.redactor, text):
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
                if config.kind not in (
                    CollectorKind.SLACK,
                    CollectorKind.GDRIVE,
                    CollectorKind.GDOCS,
                    CollectorKind.GSHEETS,
                    CollectorKind.GITHUB,
                ):
                    raise WorkflowValidationError(
                        (ValidationIssue(f"/steps/{step.id}/collector", "sweep_unsupported"),)
                    )
        reference_issues: tuple[ValidationIssue, ...] = await self._notification_reference_issues(
            session, workflow
        )
        if reference_issues:
            raise WorkflowValidationError(reference_issues)
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
                json_object(
                    (
                        ("definition_id", json_str(row.id)),
                        ("name", json_str(row.name)),
                        ("version", json_int(row.version)),
                    )
                ),
            ),
        )
        return row

    async def _notification_reference_issues(
        self, session: AsyncSession, workflow: CompiledWorkflow
    ) -> tuple[ValidationIssue, ...]:
        """Check template references and escalation-policy set names (DSL v2, API level)."""
        issues: list[ValidationIssue] = []
        references: list[tuple[str, str]] = []
        for index, notification_set in enumerate(workflow.notification_sets):
            _target_templates(
                references, notification_set.targets, f"/notification_sets/{index}/targets"
            )
        set_names: frozenset[str] = frozenset(item.name for item in workflow.notification_sets)
        for step in workflow.steps:
            path: str = f"/steps/{step.id}"
            if isinstance(step, NotifyStep):
                if step.template is not None:
                    references.append((step.template, f"{path}/template"))
                _target_templates(references, step.targets, f"{path}/targets")
            if isinstance(step, (HumanTaskStep, EvidenceUploadStep)):
                _target_templates(references, step.notify_targets, f"{path}/notify_targets")
                if step.escalation_policy is not None and await self._policy_names_unknown_set(
                    session, step.escalation_policy, set_names
                ):
                    issues.append(
                        ValidationIssue(f"{path}/escalation_policy", "unknown_notification_set")
                    )
        if self.templates is None:
            return tuple(issues)
        for name, template_path in references:
            info: TemplateInfo | None = await self.templates.lookup(session, name)
            if info is None or info.active_version is None:
                issues.append(ValidationIssue(template_path, "unknown_template"))
            elif info.event_kind is not NotificationEvent.WORKFLOW_NOTIFY:
                issues.append(ValidationIssue(template_path, "template_event_mismatch"))
        return tuple(issues)

    async def _policy_names_unknown_set(
        self, session: AsyncSession, policy_name: str, set_names: frozenset[str]
    ) -> bool:
        """Return whether a stored escalation policy names a set the workflow lacks."""
        row: EscalationPolicyRow | None = await session.scalar(
            select(EscalationPolicyRow).where(EscalationPolicyRow.name == policy_name)
        )
        if row is None:
            return False
        try:
            policy: EscalationPolicySpec = escalation_from_json(parse_json(row.tiers_json))
        except TelescreenError:
            return False
        return any(
            tier.notification_set is not None and tier.notification_set not in set_names
            for tier in policy.tiers
        )

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


def _target_templates(
    references: list[tuple[str, str]],
    targets: tuple[NotificationTargetSpec, ...],
    path: str,
) -> None:
    """Collect (template name, JSON path) pairs referenced by targets."""
    for index, target in enumerate(targets):
        if target.template is not None:
            references.append((target.template, f"{path}/{index}/template"))
