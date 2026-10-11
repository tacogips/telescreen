"""Workflow notification fan-out: notify and human executors, SLA tiers and upload checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.json import JsonValue, dump_json, json_object
from telescreen.core.vocabulary import (
    DlpVerdict,
    EngineTaskKind,
    IssueStatus,
    NotificationChannel,
    NotificationEvent,
    RuleClass,
    Severity,
    SourceFormat,
    Visibility,
)
from telescreen.db.models import (
    EscalationPolicyRow,
    HumanTaskRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import CollectorConfig
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients
from telescreen.ports.findings import ExternalScan, RecordOutcome, ResourceScan
from telescreen.ports.issues import IssueHistoryEntry, IssueSnapshot, OpenResourceRef
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.ports.templates import TemplateInfo
from telescreen.ports.workflow import ClaimedTask
from telescreen.workflow.dsl.context import RunContext, RunInfo
from telescreen.workflow.dsl.escalation import (
    EscalationPolicySpec,
    EscalationTier,
    escalation_to_json,
)
from telescreen.workflow.dsl.loader import (
    WorkflowValidationError,
    compiled_from_json,
    compiled_to_json,
    load_workflow,
)
from telescreen.workflow.dsl.model import (
    CompiledWorkflow,
    HumanTaskStep,
    NamedRoute,
    NotificationTargetSpec,
    NotifyStep,
    StepSpec,
)
from telescreen.workflow.dsl.templates import parse_template
from telescreen.workflow.engine.definitions import WorkflowService
from telescreen.workflow.engine.repository import WorkflowRepository
from telescreen.workflow.escalation.sla import SlaCheckHandler
from telescreen.workflow.executors.human import HumanTaskExecutor
from telescreen.workflow.executors.notify import NotifyExecutor
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from telescreen.workflow.step_api import StepContext, StepOutcome, StepOutcomeKind
from tests.support.db import SqliteHarness, make_sqlite_harness

NOW: datetime = datetime(2026, 10, 11, 0, 0, tzinfo=UTC)
SETS_WORKFLOW: str = """apiVersion: telescreen.workflow/v2
kind: Workflow
name: fan-out
start: alert
notification_sets:
  - name: ops
    targets:
      - {recipients: [team:ops], channels: [email, slack]}
      - {recipients: [member:alice], channels: [email, slack], template: ops-mail}
steps:
  - {id: alert, type: notify, notification_set: ops, next: done}
  - {id: done, type: end, result: succeeded}
"""


@dataclass
class SenderFake:
    """Record requests and return receipts; ``skip`` marks every row as skipped."""

    skip: bool = False
    requests: list[NotificationRequest] = field(default_factory=list)

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        del session
        self.requests.append(request)
        row_id: str = f"notification-{len(self.requests)}"
        if self.skip:
            return NotificationReceipt((row_id,), DlpVerdict.PASS, False, (row_id,))
        return NotificationReceipt((row_id,), DlpVerdict.PASS, True)


@dataclass
class ResolverFake:
    """Resolve every reference to one email address and one Slack user."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        del session, ref
        return ResolvedRecipients(("alice@example.invalid",), (), ("U-A",), ())

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        del session, member_id, ref
        return True

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        del session, ref
        return AssigneeIds("member-id", None)


@dataclass
class IssueReaderFake:
    """Return one masked issue snapshot."""

    async def get(self, session: AsyncSession, issue_id: str) -> IssueSnapshot | None:
        del session, issue_id
        return IssueSnapshot(
            "issue-1",
            8,
            "TELE-8",
            "Masked title",
            IssueStatus.UNRESOLVED,
            "github",
            RuleClass.CREDENTIAL,
            Severity.HIGH,
            "org/repo/file.txt",
            1,
            NOW,
            NOW,
            "collector-1",
            None,
            "member:alice",
            40,
            Visibility.PUBLIC,
            "****[8]",
        )

    async def open_resource_refs(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[OpenResourceRef, ...]:
        del session, collector_config_id
        return ()


@dataclass
class HistoryFake:
    """Accept issue history writes."""

    entries: list[IssueHistoryEntry] = field(default_factory=list)

    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        del session, scan
        raise NotImplementedError

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        del session, scan
        raise NotImplementedError

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        del session
        self.entries.append(entry)

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        del session, collector_config_id, checked_before
        return 0


@dataclass
class AuditFake:
    """Collect audit entries."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class ConfigFake:
    """No collector configurations."""

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        del session, collector_config_id
        return None

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        del session, name
        return None


@dataclass
class CatalogFake:
    """Known workflow templates plus one template of another event kind."""

    async def lookup(self, session: AsyncSession, name: str) -> TemplateInfo | None:
        del session
        if name == "ops-mail":
            return TemplateInfo(name, NotificationEvent.WORKFLOW_NOTIFY, 2)
        if name == "digest-mail":
            return TemplateInfo(name, NotificationEvent.OWNER_DIGEST, 1)
        if name == "draft-mail":
            return TemplateInfo(name, NotificationEvent.WORKFLOW_NOTIFY, None)
        return None


def _context(session: AsyncSession, step: StepSpec) -> StepContext:
    return StepContext(
        session,
        "run-1",
        "step-run-1",
        step,
        1,
        1,
        RunContext(None, RunInfo("run-1", "fan-out", NOW.isoformat())),
        "issue-1",
        NOW,
    )


def _notify(workflow: CompiledWorkflow) -> NotifyStep:
    step: StepSpec = workflow.step("alert")
    assert isinstance(step, NotifyStep)
    return step


@pytest.mark.asyncio
async def test_engine_decoded_set_step_fans_out_per_target_and_channel() -> None:
    """2 targets x 2 channels from the stored workflow give 4 tagged requests."""
    workflow: CompiledWorkflow = load_workflow(SETS_WORKFLOW, SourceFormat.YAML)
    decoded: CompiledWorkflow = compiled_from_json(compiled_to_json(workflow))
    sender: SenderFake = SenderFake()
    outcome: StepOutcome = await NotifyExecutor(sender, Redactor.default()).execute(
        _context(_session_stub(), _notify(decoded))
    )
    assert outcome.kind is StepOutcomeKind.COMPLETED
    assert outcome.route == "done"
    assert len(sender.requests) == 4
    assert [(item.target_index, item.channel) for item in sender.requests] == [
        (0, NotificationChannel.EMAIL),
        (0, NotificationChannel.SLACK),
        (1, NotificationChannel.EMAIL),
        (1, NotificationChannel.SLACK),
    ]
    for request in sender.requests:
        assert request.set_name == "ops"
        assert request.record_unreachable is True
        assert request.event is NotificationEvent.WORKFLOW_NOTIFY
        assert request.context is not None
        assert request.context.run_workflow == "fan-out"
        assert request.subject == "" and request.body == ""
    assert sender.requests[0].template_name == "system.workflow_notify"
    assert sender.requests[2].template_name == "ops-mail"
    output: str = dump_json(outcome.output)
    assert '"queued":true' in output.replace(" ", "")
    assert "alice" not in output and "team:ops" not in output


@pytest.mark.asyncio
async def test_template_precedence_target_step_inline_default() -> None:
    """Target template beats the step template, which beats inline text and the default."""
    targets: tuple[NotificationTargetSpec, ...] = (
        NotificationTargetSpec(("team:ops",), (NotificationChannel.EMAIL,), "target-tpl"),
        NotificationTargetSpec(("team:ops",), (NotificationChannel.EMAIL,)),
    )
    sender: SenderFake = SenderFake()
    executor: NotifyExecutor = NotifyExecutor(sender, Redactor.default())
    session: AsyncSession = _session_stub()
    await executor.execute(
        _context(
            session,
            NotifyStep(
                "alert",
                targets=targets,
                template="step-tpl",
                subject=parse_template("Leak"),
                body=parse_template("Found"),
            ),
        )
    )
    await executor.execute(
        _context(
            session,
            NotifyStep(
                "alert",
                targets=targets,
                subject=parse_template("Leak {{ run.workflow }}"),
                body=parse_template("Found"),
            ),
        )
    )
    await executor.execute(_context(session, NotifyStep("alert", targets=targets)))
    chosen: list[tuple[str | None, bool, str]] = [
        (item.template_name, item.inline, item.subject) for item in sender.requests
    ]
    assert chosen == [
        ("target-tpl", False, ""),
        ("step-tpl", False, ""),
        ("target-tpl", False, ""),
        (None, True, "Leak fan-out"),
        ("target-tpl", False, ""),
        ("system.workflow_notify", False, ""),
    ]
    assert all(item.set_name is None for item in sender.requests)


@pytest.mark.asyncio
async def test_v2_output_counts_skipped_rows_without_failing() -> None:
    """Skipped rows are counted per target and channel; the step still completes."""
    sender: SenderFake = SenderFake(skip=True)
    step: NotifyStep = NotifyStep(
        "alert",
        next="done",
        targets=(NotificationTargetSpec(("member:ghost",), (NotificationChannel.SLACK,)),),
    )
    outcome: StepOutcome = await NotifyExecutor(sender, Redactor.default()).execute(
        _context(_session_stub(), step)
    )
    assert outcome.kind is StepOutcomeKind.COMPLETED
    targets: JsonValue | None = outcome.output.get("targets")
    assert targets is not None
    first: JsonValue = targets.items[0]
    assert [
        (name, value.as_int() if value.as_int() is not None else value.as_str())
        for name, value in ((member.name, member.value) for member in first.members)
    ] == [("index", 0), ("channel", "slack"), ("queued", 0), ("blocked", 0), ("skipped", 1)]
    queued: JsonValue | None = outcome.output.get("queued")
    assert queued is not None and queued.as_bool() is False


@pytest.mark.asyncio
async def test_v1_to_form_keeps_single_inline_request_without_event() -> None:
    """The v1 ``to`` form is unchanged: one request, rendered text and no event."""
    sender: SenderFake = SenderFake()
    step: NotifyStep = NotifyStep(
        "alert",
        channel=NotificationChannel.SLACK,
        to=("team:ops",),
        subject=parse_template("Run {{ run.workflow }}"),
        body=parse_template("Body"),
    )
    await NotifyExecutor(sender, Redactor.default()).execute(_context(_session_stub(), step))
    assert len(sender.requests) == 1
    request: NotificationRequest = sender.requests[0]
    assert request.event is None and request.template_name is None
    assert request.subject == "Run fan-out" and request.body == "Body"
    assert request.record_unreachable is False and request.set_name is None


@pytest.mark.asyncio
async def test_human_task_assignment_uses_event_and_fans_out_notify_set() -> None:
    """The assignee request carries human_task_assigned; the set targets are added."""
    workflow: CompiledWorkflow = load_workflow(
        SETS_WORKFLOW.replace(
            "  - {id: alert, type: notify, notification_set: ops, next: done}\n",
            "  - id: alert\n    type: human_task\n    title: Triage\n    assignee: team:ops\n"
            "    notify_set: ops\n    outcomes: {done: done}\n",
        ),
        SourceFormat.YAML,
    )
    step: StepSpec = compiled_from_json(compiled_to_json(workflow)).step("alert")
    assert isinstance(step, HumanTaskStep)
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            sender: SenderFake = SenderFake()
            executor: HumanTaskExecutor = HumanTaskExecutor(
                sender,
                SqlTaskQueue(FakeClock(NOW)),
                ResolverFake(),
                FakeClock(NOW),
                Redactor.default(),
                issues=IssueReaderFake(),
                public_base_url="https://telescreen.example/",
            )
            outcome: StepOutcome = await executor.execute(_context(session, step))
            assert outcome.kind is StepOutcomeKind.WAITING
    finally:
        await harness.close()
    assignee: list[NotificationRequest] = [
        item for item in sender.requests if item.set_name is None
    ]
    assert [item.channel for item in assignee] == [
        NotificationChannel.EMAIL,
        NotificationChannel.SLACK,
    ]
    for request in assignee:
        assert request.event is NotificationEvent.HUMAN_TASK_ASSIGNED
        assert request.subject == "" and request.body == ""
        assert request.context is not None
        assert request.context.task_title == "Triage"
        assert request.context.assignee_ref == "team:ops"
        assert request.context.issue_short_id == "TELE-8"
        assert request.context.task_link == "https://telescreen.example/my-tasks"
    fanned: list[NotificationRequest] = [item for item in sender.requests if item.set_name]
    assert len(fanned) == 4
    assert all(item.set_name == "ops" and item.record_unreachable for item in fanned)
    assert fanned[0].template_name == "system.human_task_assigned"
    assert fanned[2].template_name == "ops-mail"


async def _seed_sla(
    harness: SqliteHarness, tier: EscalationTier, compiled_json: str | None
) -> None:
    async with harness.sessions.begin() as session:
        session.add(
            WorkflowRunRow(
                id="run-1",
                definition_id="definition-1",
                issue_id="issue-1",
                status="waiting",
                trigger="manual",
                context_json="{}",
                started_at=NOW,
            )
        )
        if compiled_json is not None:
            session.add(
                WorkflowDefinitionRow(
                    id="definition-1",
                    name="fan-out",
                    version=1,
                    source_format="json",
                    source_text=compiled_json,
                    compiled_json=compiled_json,
                    active=True,
                    created_by="member:alice",
                    created_at=NOW,
                )
            )
        session.add(
            EscalationPolicyRow(
                id="policy-1",
                name="security",
                tiers_json=dump_json(escalation_to_json(EscalationPolicySpec("security", (tier,)))),
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            HumanTaskRow(
                id="human-task-1",
                run_id="run-1",
                step_run_id="step-run-1",
                kind="approval",
                title="Review issue",
                assignee_ref="team:first-line",
                escalation_policy_id="policy-1",
                tier_index=0,
                due_at=NOW,
                status="open",
            )
        )


async def _run_sla(harness: SqliteHarness, sender: SenderFake, audit: AuditFake) -> None:
    clock: FakeClock = FakeClock(NOW)
    handler: SlaCheckHandler = SlaCheckHandler(
        harness.sessions,
        WorkflowRepository(),
        SqlTaskQueue(clock),
        sender,
        ResolverFake(),
        HistoryFake(),
        audit,
        clock,
        reader=IssueReaderFake(),
    )
    await handler.handle(
        ClaimedTask(
            "sla-task",
            EngineTaskKind.SLA_CHECK,
            "human-task-1",
            json_object(()),
            1,
            5,
            "sla-key",
        )
    )


@pytest.mark.asyncio
async def test_sla_tier_set_fans_out_with_sla_escalated_event() -> None:
    """Tier notify refs and the run workflow's set both send sla_escalated requests."""
    stored: str = dump_json(compiled_to_json(load_workflow(SETS_WORKFLOW, SourceFormat.YAML)))
    tier: EscalationTier = EscalationTier(
        timedelta(minutes=5),
        "team:on-call",
        ("team:on-call",),
        (NotificationChannel.EMAIL,),
        notification_set="ops",
    )
    harness: SqliteHarness = await make_sqlite_harness()
    sender: SenderFake = SenderFake()
    audit: AuditFake = AuditFake()
    try:
        await _seed_sla(harness, tier, stored)
        await _run_sla(harness, sender, audit)
    finally:
        await harness.close()
    assert len(sender.requests) == 5
    assert all(item.event is NotificationEvent.SLA_ESCALATED for item in sender.requests)
    assert sender.requests[0].set_name is None
    assert sender.requests[0].context is not None
    assert sender.requests[0].context.task_tier == 1
    assert sender.requests[0].context.run_workflow == "fan-out"
    assert all(item.set_name == "ops" for item in sender.requests[1:])
    assert sender.requests[1].template_name == "system.sla_escalated"
    assert not any(item.action == "notification.set_missing" for item in audit.entries)


@pytest.mark.asyncio
async def test_sla_missing_set_is_audited_and_tier_refs_still_sent() -> None:
    """A set absent from the run's workflow is skipped with a safe audit entry."""
    tier: EscalationTier = EscalationTier(
        timedelta(minutes=5),
        "team:on-call",
        ("team:on-call",),
        (NotificationChannel.SLACK,),
        notification_set="absent",
    )
    harness: SqliteHarness = await make_sqlite_harness()
    sender: SenderFake = SenderFake()
    audit: AuditFake = AuditFake()
    try:
        await _seed_sla(harness, tier, None)
        await _run_sla(harness, sender, audit)
    finally:
        await harness.close()
    assert len(sender.requests) == 1
    assert sender.requests[0].event is NotificationEvent.SLA_ESCALATED
    assert sender.requests[0].channel is NotificationChannel.SLACK
    missing: list[AuditEntry] = [
        item for item in audit.entries if item.action == "notification.set_missing"
    ]
    assert len(missing) == 1
    assert missing[0].target_kind == "human_task"
    assert dump_json(missing[0].details).replace(" ", "") == '{"set_name":"absent"}'


def _service(audit: AuditFake, catalog: CatalogFake | None) -> WorkflowService:
    return WorkflowService(
        Redactor.default(), ConfigFake(), audit, FakeClock(NOW), WorkflowRepository(), catalog
    )


async def _upload_codes(source: str, catalog: CatalogFake | None) -> tuple[tuple[str, str], ...]:
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions.begin() as session:
            session.add(
                EscalationPolicyRow(
                    id="policy-1",
                    name="security",
                    tiers_json=dump_json(
                        escalation_to_json(
                            EscalationPolicySpec(
                                "security",
                                (
                                    EscalationTier(
                                        timedelta(minutes=5),
                                        "team:on-call",
                                        (),
                                        (),
                                        notification_set="undeclared",
                                    ),
                                ),
                            )
                        )
                    ),
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        async with harness.sessions.begin() as session:
            try:
                await _service(AuditFake(), catalog).upload(
                    session, SourceFormat.YAML, source, "member:alice"
                )
            except WorkflowValidationError as error:
                return tuple((item.code, item.path) for item in error.issues)
        return ()
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_upload_checks_templates_and_escalation_sets() -> None:
    """API upload rejects unknown, inactive and wrong-event templates and undeclared sets."""
    assert await _upload_codes(SETS_WORKFLOW, CatalogFake()) == ()
    unknown: str = SETS_WORKFLOW.replace(
        "notification_set: ops,", "notification_set: ops, template: nope,"
    )
    assert await _upload_codes(unknown, CatalogFake()) == (
        ("unknown_template", "/steps/alert/template"),
    )
    inactive: str = SETS_WORKFLOW.replace("template: ops-mail", "template: draft-mail")
    assert await _upload_codes(inactive, CatalogFake()) == (
        ("unknown_template", "/notification_sets/0/targets/1/template"),
    )
    mismatch: str = SETS_WORKFLOW.replace("template: ops-mail", "template: digest-mail")
    assert await _upload_codes(mismatch, CatalogFake()) == (
        ("template_event_mismatch", "/notification_sets/0/targets/1/template"),
    )
    assert await _upload_codes(unknown, None) == ()
    escalated: str = SETS_WORKFLOW.replace(
        "  - {id: alert, type: notify, notification_set: ops, next: done}\n",
        "  - id: alert\n    type: human_task\n    title: Triage\n    assignee: team:ops\n"
        "    escalation_policy: security\n    outcomes: {done: done}\n",
    )
    assert await _upload_codes(escalated, None) == (
        ("unknown_notification_set", "/steps/alert/escalation_policy"),
    )


def _session_stub() -> AsyncSession:
    """Return an unbound session; notify fan-out only passes it to the sender fake."""
    return AsyncSession()


def test_human_step_defaults_keep_v1_shape() -> None:
    """New v2 fields default to empty so v1 constructors keep working."""
    step: HumanTaskStep = HumanTaskStep("review", outcomes=(NamedRoute("done", "done"),))
    assert step.notify_set is None and step.notify_targets == () and step.set_targets == ()
