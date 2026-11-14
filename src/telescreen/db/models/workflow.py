"""Workflow definitions, runs, transitions and task database models."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class WorkflowDefinitionRow(Base, kw_only=True):
    """An immutable version of a validated workflow definition."""

    __tablename__ = "workflow_definitions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_workflow_definition_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    source_format: Mapped[str] = mapped_column(String(32))
    source_text: Mapped[str] = mapped_column(Text, repr=False)
    compiled_json: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EscalationPolicyRow(Base, kw_only=True):
    """A named escalation policy with serialized tiers."""

    __tablename__ = "escalation_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    tiers_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkflowRunRow(Base, kw_only=True):
    """A durable workflow execution."""

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    definition_id: Mapped[str] = mapped_column(String(36), index=True)
    issue_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    parent_step_run_id: Mapped[str | None] = mapped_column(String(36), default=None)
    status: Mapped[str] = mapped_column(String(32), index=True)
    trigger: Mapped[str] = mapped_column(String(32))
    context_json: Mapped[str] = mapped_column(Text)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class StepRunRow(Base, kw_only=True):
    """A single visit and attempt at one workflow step."""

    __tablename__ = "step_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "step_id", "visit", "branch_token", name="uq_step_run_visit"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    step_id: Mapped[str] = mapped_column(String(128))
    visit: Mapped[int] = mapped_column(Integer)
    branch_token: Mapped[str | None] = mapped_column(String(128), default=None)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input_json: Mapped[str] = mapped_column(Text)
    output_json: Mapped[str | None] = mapped_column(Text, default=None)
    outcome: Mapped[str | None] = mapped_column(String(32), default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class TransitionRow(Base, kw_only=True):
    """A workflow run's directed transition history."""

    __tablename__ = "transitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    from_step_run_id: Mapped[str | None] = mapped_column(String(36), default=None)
    to_step_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EngineTaskRow(Base, kw_only=True):
    """A leased durable engine queue item."""

    __tablename__ = "engine_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    ref_id: Mapped[str] = mapped_column(String(36), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HumanTaskRow(Base, kw_only=True):
    """A human approval or evidence task with optional due time."""

    __tablename__ = "human_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    step_run_id: Mapped[str] = mapped_column(String(36), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(512))
    assignee_ref: Mapped[str] = mapped_column(String(256))
    escalation_policy_id: Mapped[str | None] = mapped_column(String(36), default=None)
    tier_index: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    outcome: Mapped[str | None] = mapped_column(String(32), default=None)
    comment: Mapped[str | None] = mapped_column(Text, default=None)
    completed_by: Mapped[str | None] = mapped_column(String(128), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
