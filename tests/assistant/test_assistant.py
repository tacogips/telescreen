"""Assistant tool, MCP, chat-loop, and API boundary tests."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from httpx import Response as HttpxResponse
from mcp.server.fastmcp import FastMCP
from mcp.types import Tool as McpTool
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.common.auth import hash_token
from telescreen.api.routers.chat import ChatRouterDeps, build_chat_router
from telescreen.assistant.chat import ChatMessage, ChatRole, ChatService
from telescreen.assistant.mcp_server import build_mcp
from telescreen.assistant.tools import (
    AssistantTools,
    AuditList,
    GroupBy,
    IncidentCounts,
    IssueDetail,
    IssueList,
    RunList,
)
from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import ApiScope, ErrorCode, IssueStatus, RunStatus
from telescreen.db.models import (
    ApiTokenRow,
    AuditRecordRow,
    IssueHistoryRow,
    IssueRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.detection.harness import LlmJsonRequest
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, github_token


@dataclass
class FakeLlmClient:
    """Queue JSON answers and retain requests for safe-boundary assertions."""

    responses: list[str]
    requests: list[LlmJsonRequest]

    async def complete_json(self, request: LlmJsonRequest) -> str:
        """Return the next canned JSON response."""
        self.requests.append(request)
        return self.responses.pop(0)


@dataclass
class FakeAudit:
    """No-op audit implementation used by the chat router context."""

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Satisfy the shared API context contract without writing data."""
        del session, entry
        return 1


def _issue(
    issue_id: str, title: str, status: IssueStatus, first_seen: datetime, number: int
) -> IssueRow:
    """Build a minimal issue row for assistant query tests."""
    return IssueRow(
        id=issue_id,
        number=number,
        grouping_key=f"group-{issue_id}",
        source="github",
        resource_key=f"resource-{issue_id}",
        display_locator=f"repo/{issue_id}",
        display_title=title,
        rule_class="token",
        severity="high",
        status=status.value,
        first_seen=first_seen,
        last_seen=first_seen,
        last_checked_at=first_seen,
        created_at=first_seen,
        updated_at=first_seen,
        times_seen=3,
    )


async def _seed(harness: SqliteHarness, sample: Sample) -> None:
    """Seed two issues, audit history, and one workflow run."""
    last_month: datetime = datetime(2026, 9, 15, tzinfo=UTC)
    this_month: datetime = datetime(2026, 10, 5, tzinfo=UTC)
    async with harness.sessions.begin() as session:
        session.add(_issue("issue-1", sample.value, IssueStatus.UNRESOLVED, last_month, 1))
        session.add(_issue("issue-2", "Known issue", IssueStatus.RESOLVED, last_month, 2))
        session.add(_issue("issue-3", "Recent issue", IssueStatus.IN_PROGRESS, this_month, 3))
        session.add(
            IssueHistoryRow(
                id="history-1",
                issue_id="issue-1",
                kind="created",
                actor_ref="system",
                note=sample.value,
                at=last_month,
            )
        )
        session.add(
            AuditRecordRow(
                seq=1,
                id="audit-1",
                at=last_month,
                actor_kind="system",
                actor_ref="system",
                action="issue.created",
                target_kind="issue",
                target_id="issue-1",
                summary=f"Detected {sample.value}",
                details_json="{}",
                prev_hash="0" * 64,
                hash="1" * 64,
            )
        )
        session.add(
            WorkflowDefinitionRow(
                id="workflow-def-1",
                name="remediate",
                version=1,
                source_format="yaml",
                source_text="steps: []",
                compiled_json="{}",
                created_by="test",
                created_at=last_month,
            )
        )
        session.add(
            WorkflowRunRow(
                id="run-1",
                definition_id="workflow-def-1",
                issue_id="issue-1",
                status=RunStatus.RUNNING.value,
                trigger="manual",
                context_json="{}",
                started_at=last_month,
            )
        )


def _tools(harness: SqliteHarness, clock: FakeClock | None = None) -> AssistantTools:
    """Construct assistant tools over the test database."""
    configured_clock: FakeClock = clock or FakeClock(datetime(2026, 10, 8, tzinfo=UTC))
    return AssistantTools(harness.sessions, Redactor.default(), configured_clock)


@pytest.mark.asyncio
async def test_assistant_tools_redact_results_and_filter_by_status() -> None:
    """Issue, audit, run, and count results expose only redacted strings."""
    sample: Sample = github_token()
    harness: SqliteHarness = await make_sqlite_harness()
    await _seed(harness, sample)
    tools: AssistantTools = _tools(harness)
    try:
        issues: IssueList = await tools.search_issues("", IssueStatus.UNRESOLVED, 50)
        assert len(issues.items) == 1
        assert sample.value not in tools.render_json(issues)
        assert "****************[42]" in tools.render_json(issues)

        detail: IssueDetail | None = await tools.get_issue("issue-1")
        assert detail is not None
        assert sample.value not in tools.render_json(detail)
        assert detail.history[0].note is not None
        assert "****************[42]" in detail.history[0].note

        counts: IncidentCounts = await tools.count_incidents(
            datetime(2026, 9, 1, tzinfo=UTC),
            datetime(2026, 10, 1, tzinfo=UTC),
            GroupBy.SEVERITY,
        )
        assert counts.total == 2
        assert counts.items[0].key == "high"

        runs: RunList = await tools.list_workflow_runs(RunStatus.RUNNING, 10)
        assert runs.items[0].workflow == "remediate"
        audit: AuditList = await tools.audit_search("issue.created", 10)
        assert len(audit.items) == 1
        assert sample.value not in tools.render_json(audit)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_incident_report_contains_counts_and_redacts_seeded_values() -> None:
    """Markdown reports include severity, rule, resource and open/resolved totals."""
    sample: Sample = github_token()
    harness: SqliteHarness = await make_sqlite_harness()
    await _seed(harness, sample)
    tools: AssistantTools = _tools(harness)
    try:
        report: str = await tools.incident_report(
            datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)
        )
        assert "Total incidents: 2" in report
        assert "Resolved or ignored: 1; open: 1" in report
        assert "By severity" in report
        assert "By rule class" in report
        assert sample.value not in report
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_chat_tool_loop_redacts_transcript_results_and_final_answer() -> None:
    """A tool result reaches the next model request only as safe JSON."""
    sample: Sample = github_token()
    harness: SqliteHarness = await make_sqlite_harness()
    await _seed(harness, sample)
    tools: AssistantTools = _tools(harness)
    first: str = json.dumps(
        {
            "action": "tool",
            "tool": "count_incidents",
            "arguments": {
                "start": "2026-09-01T00:00:00+00:00",
                "end": "2026-10-01T00:00:00+00:00",
                "group_by": "severity",
            },
        }
    )
    client: FakeLlmClient = FakeLlmClient(
        [first, json.dumps({"action": "answer", "answer": f"Found {sample.value}"})], []
    )
    service: ChatService = ChatService(
        client,
        "test-model",
        tools,
        Redactor.default(),
        FakeClock(datetime(2026, 10, 8, tzinfo=UTC)),
    )
    try:
        answer: str = await service.answer((ChatMessage(ChatRole.USER, sample.value),))
        assert sample.value not in client.requests[0].user
        assert "****************[42]" in client.requests[0].user
        assert r"\"total\": 2" in client.requests[1].user
        assert sample.value not in answer
        assert "****************[42]" in answer
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_chat_invalid_tool_note_and_seven_tool_round_limit() -> None:
    """Invalid JSON steps receive a static note and the seventh tool call stops."""
    harness: SqliteHarness = await make_sqlite_harness()
    tools: AssistantTools = _tools(harness)
    invalid: str = json.dumps(
        {"action": "tool", "tool": "unknown_tool", "arguments": {"query": "safe"}}
    )
    seven_calls: list[str] = [
        json.dumps({"action": "tool", "tool": "get_issue", "arguments": {"issue_id": "missing"}})
        for _ in range(7)
    ]
    fake_invalid: FakeLlmClient = FakeLlmClient(
        [invalid, json.dumps({"action": "answer", "answer": "done"})], []
    )
    fake_limited: FakeLlmClient = FakeLlmClient(seven_calls, [])
    try:
        invalid_service: ChatService = ChatService(
            fake_invalid,
            None,
            tools,
            Redactor.default(),
            FakeClock(datetime(2026, 10, 8, tzinfo=UTC)),
        )
        assert await invalid_service.answer((ChatMessage(ChatRole.USER, "hello"),)) == "done"
        assert "previous tool request was invalid" in fake_invalid.requests[1].user
        limited_service: ChatService = ChatService(
            fake_limited,
            None,
            tools,
            Redactor.default(),
            FakeClock(datetime(2026, 10, 8, tzinfo=UTC)),
        )
        assert await limited_service.answer((ChatMessage(ChatRole.USER, "hello"),)) == (
            "I could not complete that request within the tool limit."
        )
        assert len(fake_limited.requests) == 7
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_mcp_registers_exactly_six_tools() -> None:
    """The public FastMCP tool registry contains exactly the planned names."""
    harness: SqliteHarness = await make_sqlite_harness()
    server: FastMCP[None] = build_mcp(_tools(harness))
    try:
        registered: list[McpTool] = await server.list_tools()
        assert {tool.name for tool in registered} == {
            "search_issues",
            "get_issue",
            "count_incidents",
            "incident_report",
            "list_workflow_runs",
            "audit_search",
        }
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_chat_router_returns_503_when_client_is_unavailable() -> None:
    """The authenticated HTTP route maps a missing managed LLM to 503."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 8, tzinfo=UTC))
    ctx: ApiContext = ApiContext(harness.sessions, clock, FakeAudit())
    tools: AssistantTools = _tools(harness, clock)
    service: ChatService = ChatService(None, None, tools, Redactor.default(), clock)
    async with harness.sessions.begin() as session:
        session.add(
            ApiTokenRow(
                id="read-token",
                label="read",
                token_hash=hash_token("test-api-token"),
                scopes=ApiScope.READ.value,
                created_at=clock.now(),
            )
        )
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(build_chat_router(ChatRouterDeps(ctx, service)))
    transport: ASGITransport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response: HttpxResponse = await client.post(
                "/api/chat",
                headers={"Authorization": "Bearer test-api-token"},
                json={"messages": [{"role": "user", "content": "hello"}]},
            )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == ErrorCode.LLM_UNAVAILABLE.value
    finally:
        await harness.close()
