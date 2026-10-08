# P27 Assistant (MCP Server and Agent Chat) Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-api-and-ui.md (Assistant), design-docs/specs/architecture.md section 7 item 7
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p27-assistant`
- planPath: `impl-plans/active/p27-assistant.md`
- wave: 4
- dependsOn: `p08-masking`, `p09-detection-llm-ml`, `p10-api-common`
- writePaths:
  - `impl-plans/active/p27-assistant.md`
  - `src/telescreen/assistant`
  - `src/telescreen/api/routers/chat.py`
  - `tests/assistant`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

The Bureau can be queried by AI (requirement 3). It offers:

- an MCP server (`telescreen mcp`, stdio, official `mcp` SDK `FastMCP`);
- `POST /api/chat`, an agent tool loop on the managed LLM (Claude by default, or Codex).

Both use one typed tool set whose outputs are masked data passed through `Redactor` as a
final guard. Example question: "How many incidents last month?" produces a report.

## Non-goals

- No chat history storage (the client resends the transcript). No provider-native tool
  calling: the loop uses JSON `ChatStep`s.
- Read-only access: this package may run read-only queries over `IssueRow`,
  `FindingRow`, `WorkflowRunRow`, `WorkflowDefinitionRow` and `AuditRecordRow` through
  `telescreen.db.models` (planning decision: reporting component). It never writes.

## Deliverables (`src/telescreen/assistant/`)

- `tools.py`: `AssistantTools(sessions, redactor: Redactor, clock)` with typed async
  methods that return dataclasses:
  - `search_issues(query: str, status: IssueStatus | None, limit: int) -> IssueList`
    (SQL `LIKE` over `display_title` and `display_locator`; limit 1-50);
  - `get_issue(issue_id: str) -> IssueDetail | None` (masked columns plus the last 10
    events and the history);
  - `count_incidents(start: datetime, end: datetime, group_by: GroupBy) -> IncidentCounts`,
    where `GroupBy` is status, source, rule_class or severity, counting issues with
    `first_seen` in `[start, end)`;
  - `incident_report(start, end) -> str`: a markdown report with totals, a by-severity
    table, a by-rule-class table, the top 10 resources by `times_seen` (display locators
    only), and resolved versus open counts;
  - `list_workflow_runs(status: RunStatus | None, limit: int) -> RunList`;
  - `audit_search(query: str, limit: int) -> AuditList` (SQL `LIKE` over `action` and
    `summary`).
  - Every string field of every result goes through `redactor.redact` before it is
    returned.
  - `render_json(result) -> str` uses `dataclasses.asdict` inline into `json.dumps`
    (sort_keys).
- `mcp_server.py`: `build_mcp(tools: AssistantTools) -> FastMCP` registers six tools with
  typed parameters (str, int, ISO date strings parsed with `datetime.fromisoformat`) that
  return JSON or markdown text. `run_stdio(tools) -> None` (used by `telescreen mcp`).
- `chat.py`:
  - `ChatMessage(role: ChatRole (user|assistant), content: str = field(repr=False))`.
  - `ChatStep`, parsed with `TypeAdapter`: `{action: "tool", tool: ToolName, arguments: {...}}`
    or `{action: "answer", answer: str}`. Per-tool argument dataclasses:
    `SearchIssuesArgs`, `GetIssueArgs`, `CountIncidentsArgs`, `IncidentReportArgs`,
    `ListRunsArgs`, `AuditSearchArgs`.
  - `ChatService(client: LlmClient | None, model: str | None, tools: AssistantTools, redactor, max_rounds: int = 6)`
    with `answer(messages: Sequence[ChatMessage]) -> str`:
    1. No client -> `TelescreenError(llm_unavailable)` (503).
    2. Redact user messages.
    3. The system prompt describes the tools and their JSON schemas, today's date (from
       the clock) and the rule "never reveal raw values".
    4. Each round calls `complete_json` with the `ChatStep` schema. A tool step runs the
       tool and appends its masked result to the transcript; invalid arguments append an
       error note; an answer step stops the loop.
    5. More than 6 rounds -> return a fixed "could not complete" text.
    6. The final answer is redacted.
- Router `routers/chat.py`: `ChatRouterDeps(ctx, chat: ChatService)`;
  `POST /api/chat` (read) with body `{messages: [{role, content}]}` (at most 50
  messages, 20000 characters each) -> `{answer}`.

## Key Pitfalls

- The transcript sent to the LLM contains only redacted user text and masked tool
  results, never DB values that were not redacted.
- Never log messages or answers.
- `mcp` typing: if `FastMCP.tool()` decorator typing fails ty, wrap the registration in a
  small typed adapter function (p01's spike shows the working pattern). Never add a
  suppression.
- Date arithmetic uses UTC (`ensure_utc`).

## Tests (`tests/assistant/`)

SQLite harness seeded with issues (some with `first_seen` last month), runs and audit
rows; one issue title is seeded with a sample secret inserted directly to test the final
guard.

- `count_incidents(last month, group_by=severity)` -> correct counts.
- `incident_report` contains totals and no leak, even with the seeded raw title.
- `search_issues` limit and status filter.
- Chat with a fake `LlmClient`: round 1 returns a `count_incidents` tool step, round 2 an
  answer -> the answer is returned; the second request's user prompt contains the tool
  result JSON. A user message containing a sample is sent redacted (inspect the fake's
  requests). 7 tool rounds -> the fixed text. An invalid tool name -> an error note
  appended and the loop continues. No client -> 503 via the router.
- `build_mcp` registers exactly the six tool names (inspect the FastMCP tool list API).

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen/assistant src/telescreen/api/routers/chat.py tests/assistant"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/assistant 2>&1 | tee /tmp/telescreen-p27-assistant-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p27-assistant-typelint.log 2>&1; grep -E '^(src/telescreen/assistant|src/telescreen/api/routers/chat.py|tests/assistant)' /tmp/telescreen-p27-assistant-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] AssistantTools (six tools), MCP server builder, ChatService and the chat router delivered with the exact names
- [ ] Leak guard and tool-loop tests pass
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
