"""Behavior tests for the workflow's external-effect executors."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_int, json_object, json_str
from telescreen.core.vocabulary import (
    DetectorKind,
    DlpVerdict,
    ErrorCode,
    NotificationChannel,
    PoliceTaskStatus,
)
from telescreen.db.models.police import PoliceTaskRow
from telescreen.detection.harness import LlmJsonRequest
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import RescanKind, RescanResult
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.ports.police import PoliceTaskRequest
from telescreen.ports.workflow import StepSignal, StepSignalKind
from telescreen.workflow.dsl.context import IssueContext, RunContext, RunInfo
from telescreen.workflow.dsl.model import (
    AgentProvider,
    AiAgentStep,
    DetectStep,
    NamedTemplate,
    NotifyStep,
    Outcomes3,
    OutputField,
    OutputType,
    PoliceVerifyStep,
    StepSpec,
)
from telescreen.workflow.dsl.templates import parse_template
from telescreen.workflow.executors.ai_agent import AiAgentExecutor
from telescreen.workflow.executors.detect import DetectExecutor
from telescreen.workflow.executors.notify import NotifyExecutor
from telescreen.workflow.executors.police_verify import PoliceVerifyExecutor
from telescreen.workflow.step_api import StepContext, StepOutcome, StepOutcomeKind, WaitKind
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, github_token


@dataclass
class FakeRescanner:
    """Record rescan requests and return a fixed safe result."""

    result: RescanResult
    issue_id: str | None = None
    detectors: tuple[DetectorKind, ...] | None = None

    async def rescan(
        self, issue_id: str, detectors: tuple[DetectorKind, ...] | None = None
    ) -> RescanResult:
        """Return the configured result."""
        self.issue_id = issue_id
        self.detectors = detectors
        return self.result


@dataclass
class FakeLlm:
    """Record requests and return configured responses in order."""

    responses: list[str]
    requests: list[LlmJsonRequest] = field(default_factory=list)
    failure: bool = False

    async def complete_json(self, request: LlmJsonRequest) -> str:
        """Return one completion or raise a safe provider error."""
        self.requests.append(request)
        if self.failure:
            raise TelescreenError(ErrorCode.PROVIDER_ERROR, "provider failed")
        return self.responses.pop(0)


@dataclass
class FakeSender:
    """Record a notification and return a fixed DLP receipt."""

    receipt: NotificationReceipt
    request: NotificationRequest | None = None

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Record the request and return the configured receipt."""
        del session
        self.request = request
        return self.receipt


@dataclass
class FakePolice:
    """Record Police dispatch and cancellation calls."""

    request: PoliceTaskRequest | None = None
    cancelled: list[str] = field(default_factory=list)

    async def dispatch(self, session: AsyncSession, request: PoliceTaskRequest) -> str:
        """Record a dispatch and return its opaque id."""
        del session
        self.request = request
        return "task-id"

    async def cancel(self, session: AsyncSession, task_id: str) -> None:
        """Record a cancellation."""
        del session
        self.cancelled.append(task_id)


def _context(session: AsyncSession, step: StepSpec, has_issue: bool = True) -> StepContext:
    """Create a direct executor context with safe issue fields."""
    issue: IssueContext | None = (
        IssueContext(
            8,
            "TSC-8",
            "credential finding",
            "unresolved",
            "github",
            "credential",
            "high",
            "org/repo/file",
            2,
            "2026-10-08T00:00:00Z",
            "2026-10-09T00:00:00Z",
        )
        if has_issue
        else None
    )
    context: RunContext = RunContext(issue, RunInfo("run-1", "test-flow", "2026-10-08T00:00:00Z"))
    return StepContext(
        session,
        "run-1",
        "step-run-1",
        step,
        1,
        1,
        context,
        "issue-1" if has_issue else None,
        datetime(2026, 10, 9, tzinfo=UTC),
    )


def _field(value: JsonValue, name: str) -> JsonValue:
    """Read a required JSON object field in a test assertion."""
    field_value: JsonValue | None = value.get(name)
    assert field_value is not None
    return field_value


def _string(value: JsonValue, name: str) -> str:
    """Read a required JSON string field in a test assertion."""
    field_value: str | None = _field(value, name).as_str()
    assert field_value is not None
    return field_value


@pytest.mark.asyncio
async def test_detect_routes_found_and_passes_detector_selection() -> None:
    """Detect maps a rescan result to the matching route."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            rescanner: FakeRescanner = FakeRescanner(RescanResult(RescanKind.FOUND))
            step: DetectStep = DetectStep(
                "scan",
                detectors=(DetectorKind.RULE, DetectorKind.MANAGED_LLM),
                outcomes=Outcomes3(found="found", clean="clean", error="error"),
            )
            outcome: StepOutcome = await DetectExecutor(rescanner).execute(_context(session, step))
            assert outcome.route == "found"
            assert _field(outcome.output, "result").as_str() == "found"
            assert rescanner.issue_id == "issue-1"
            assert rescanner.detectors == (DetectorKind.RULE, DetectorKind.MANAGED_LLM)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_detect_routes_clean_and_rescan_error() -> None:
    """Clean and failed rescans use their own declared routes and result codes."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            step: DetectStep = DetectStep(
                "scan", outcomes=Outcomes3(found="found", clean="clean", error="error")
            )
            cases: tuple[tuple[RescanResult, str, ErrorCode | None], ...] = (
                (RescanResult(RescanKind.CLEAN), "clean", None),
                (
                    RescanResult(RescanKind.ERROR, ErrorCode.FETCH_FAILED),
                    "error",
                    ErrorCode.FETCH_FAILED,
                ),
            )
            for result, route, error_code in cases:
                outcome: StepOutcome = await DetectExecutor(FakeRescanner(result)).execute(
                    _context(session, step)
                )
                assert outcome.route == route
                if error_code is not None:
                    assert _field(outcome.output, "error_code").as_str() == error_code.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_detect_without_issue_routes_not_rescannable() -> None:
    """A detect step with no issue takes the configured error route."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            step: DetectStep = DetectStep("scan", outcomes=Outcomes3(error="failed"))
            outcome: StepOutcome = await DetectExecutor(
                FakeRescanner(RescanResult(RescanKind.CLEAN))
            ).execute(_context(session, step, has_issue=False))
            assert outcome.route == "failed"
            assert _field(outcome.output, "error_code").as_str() == ErrorCode.NOT_RESCANNABLE.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ai_agent_redacts_prompt_and_string_output() -> None:
    """Agent calls receive a masked prompt and persist masked string fields."""
    harness: SqliteHarness = await make_sqlite_harness()
    sample: Sample = github_token()
    try:
        async with harness.sessions() as session:
            client: FakeLlm = FakeLlm([f'{{"review":"{sample.value}"}}'])
            step: AiAgentStep = AiAgentStep(
                "agent",
                prompt=parse_template("Review {{ issue.title }} {{ issue.short_id }}"),
                output=(OutputField("review", OutputType.STRING),),
                next="next",
            )
            ctx: StepContext = _context(session, step)
            issue: IssueContext | None = ctx.context.issue
            assert issue is not None
            ctx = StepContext(
                ctx.session,
                ctx.run_id,
                ctx.step_run_id,
                ctx.step,
                ctx.visit,
                ctx.attempt,
                RunContext(
                    IssueContext(
                        issue.number,
                        issue.short_id,
                        sample.value,
                        issue.status,
                        issue.source,
                        issue.rule_class,
                        issue.severity,
                        issue.display_locator,
                        issue.times_seen,
                        issue.first_seen,
                        issue.last_seen,
                    ),
                    ctx.context.run,
                ),
                ctx.issue_id,
                ctx.now,
            )
            outcome: StepOutcome = await AiAgentExecutor(
                client, None, "managed-model", None, Redactor.default()
            ).execute(ctx)
            assert outcome.route == "next"
            assert sample.value not in client.requests[0].user
            assert "TSC-8" in client.requests[0].user
            assert sample.value not in _string(outcome.output, "review")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ai_agent_retries_invalid_output_three_times() -> None:
    """Invalid completion objects are retried twice, then fail safely."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            client: FakeLlm = FakeLlm(['{"extra":true}', '{"verified":"yes"}', "{}"])
            step: AiAgentStep = AiAgentStep(
                "agent", output=(OutputField("verified", OutputType.BOOLEAN),)
            )
            outcome: StepOutcome = await AiAgentExecutor(
                client, None, None, None, Redactor.default()
            ).execute(_context(session, step))
            assert outcome.kind is StepOutcomeKind.FAILED
            assert outcome.error_code is ErrorCode.LLM_OUTPUT_INVALID
            assert outcome.retryable is False
            assert len(client.requests) == 3
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ai_agent_provider_unavailable_and_provider_error() -> None:
    """Missing clients fail permanently and provider failures remain retryable."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            local_step: AiAgentStep = AiAgentStep("agent", provider=AgentProvider.LOCAL)
            unavailable: StepOutcome = await AiAgentExecutor(
                None, None, None, None, Redactor.default()
            ).execute(_context(session, local_step))
            assert unavailable.error_code is ErrorCode.LLM_UNAVAILABLE
            failed_client: FakeLlm = FakeLlm([], failure=True)
            managed_step: AiAgentStep = AiAgentStep(
                "agent", output=(OutputField("ok", OutputType.BOOLEAN),)
            )
            failed: StepOutcome = await AiAgentExecutor(
                failed_client, None, None, None, Redactor.default()
            ).execute(_context(session, managed_step))
            assert failed.error_code is ErrorCode.PROVIDER_ERROR
            assert failed.retryable is True
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_notify_redacts_request_and_returns_queued_ids() -> None:
    """Notifications use redacted rendered text and return safe ids."""
    harness: SqliteHarness = await make_sqlite_harness()
    sample: Sample = github_token()
    try:
        async with harness.sessions() as session:
            sender: FakeSender = FakeSender(
                NotificationReceipt(("notification-1",), DlpVerdict.PASS, True)
            )
            step: NotifyStep = NotifyStep(
                "notify",
                channel=NotificationChannel.EMAIL,
                to=("team:security",),
                subject=parse_template("Issue {{ issue.short_id }}"),
                body=parse_template(sample.value),
                next="next",
            )
            outcome: StepOutcome = await NotifyExecutor(sender, Redactor.default()).execute(
                _context(session, step)
            )
            assert outcome.route == "next"
            assert sender.request is not None
            assert sample.value not in sender.request.body
            assert sender.request.subject == "Issue TSC-8"
            notification_ids: tuple[JsonValue, ...] | None = _field(
                outcome.output, "notification_ids"
            ).as_list()
            assert notification_ids is not None
            assert notification_ids[0].as_str() == "notification-1"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_notify_blocked_and_unknown_recipient() -> None:
    """Blocked DLP receipts complete safely; unknown recipients fail terminally."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            step: NotifyStep = NotifyStep("notify", next="next")
            blocked: StepOutcome = await NotifyExecutor(
                FakeSender(NotificationReceipt((), DlpVerdict.BLOCKED, False)), Redactor.default()
            ).execute(_context(session, step))
            assert _field(blocked.output, "queued").as_bool() is False
            assert _field(blocked.output, "delivered").as_bool() is False

            @dataclass
            class UnknownSender:
                async def send(
                    self, session: AsyncSession, request: NotificationRequest
                ) -> NotificationReceipt:
                    """Raise a static unknown-recipient error."""
                    del session, request
                    raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "unknown")

            unknown: StepOutcome = await NotifyExecutor(
                UnknownSender(), Redactor.default()
            ).execute(_context(session, step))
            assert unknown.error_code is ErrorCode.RECIPIENT_UNKNOWN
            assert unknown.retryable is False
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_police_dispatches_masked_issue_payload_and_resumes() -> None:
    """Police dispatch includes the safe issue fields and declared timeout."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            gateway: FakePolice = FakePolice()
            step: PoliceVerifyStep = PoliceVerifyStep(
                "verify",
                task_type="verify_remediation",
                labels=("github",),
                input=(
                    NamedTemplate("instructions", parse_template("Check {{ issue.short_id }}")),
                ),
                timeout=timedelta(minutes=5),
                outcomes=Outcomes3(passed="passed", failed="failed", error="error"),
            )
            ctx: StepContext = _context(session, step)
            executor: PoliceVerifyExecutor = PoliceVerifyExecutor(gateway, Redactor.default())
            waiting: StepOutcome = await executor.execute(ctx)
            assert waiting.wait_kind is WaitKind.POLICE
            assert gateway.request is not None
            assert gateway.request.deadline == ctx.now + timedelta(minutes=5)
            assert gateway.request.step_run_id == ctx.step_run_id
            issue_payload: JsonValue = _field(gateway.request.input, "issue")
            assert _field(issue_payload, "short_id").as_str() == "TSC-8"
            assert _field(gateway.request.input, "instructions").as_str() == "Check TSC-8"
            signal: StepSignal = StepSignal(
                StepSignalKind.POLICE_RESULT,
                "passed",
                json_object((("summary", json_str("verified")), ("findings_count", json_int(0)))),
            )
            resumed: StepOutcome = await executor.resume(ctx, signal)
            assert resumed.route == "passed"
            assert _field(resumed.output, "summary").as_str() == "verified"
            assert _field(resumed.output, "findings_count").as_int() == 0
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_police_timer_cancels_nonterminal_linked_task() -> None:
    """A timer cancels an outstanding linked Police task before routing error."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            ctx: StepContext = _context(
                session, PoliceVerifyStep("verify", outcomes=Outcomes3(error="error"))
            )
            session.add(
                PoliceTaskRow(
                    id="task-linked",
                    task_type="verify_remediation",
                    labels="[]",
                    input_json="{}",
                    status=PoliceTaskStatus.LEASED.value,
                    deadline=ctx.now + timedelta(minutes=1),
                    created_at=ctx.now,
                    step_run_id=ctx.step_run_id,
                )
            )
            await session.flush()
            gateway: FakePolice = FakePolice()
            outcome: StepOutcome = await PoliceVerifyExecutor(gateway, Redactor.default()).resume(
                ctx, StepSignal(StepSignalKind.TIMER_FIRED)
            )
            assert outcome.route == "error"
            assert _field(outcome.output, "timed_out").as_bool() is True
            assert gateway.cancelled == ["task-linked"]
    finally:
        await harness.close()
