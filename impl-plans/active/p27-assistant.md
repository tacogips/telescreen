# P27 Assistant (MCP Server and Agent Chat) Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-api-and-ui.md (Assistant), design-docs/specs/architecture.md section 7 item 7
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

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
uv run --frozen python tools/typelint.py > /tmp/telescreen-p27-assistant-typelint.log 2>&1; ! grep -E '(/src/telescreen/assistant/|/src/telescreen/api/routers/chat\.py|/tests/assistant/)' /tmp/telescreen-p27-assistant-typelint.log   # typelint prints absolute paths; prints nothing for p27
```

## Completion Criteria

- [x] AssistantTools (six tools), MCP server builder, ChatService and the chat router delivered with the exact names
- [x] Leak guard and tool-loop tests pass
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 08:20 JST
**Tasks Completed**: Implemented all six read-only assistant tools, FastMCP registration and stdio entrypoint, typed JSON tool-loop ChatService, and authenticated `/api/chat` router. Added six SQLite-backed assistant tests covering redaction of seeded sensitive values, query/count/report behavior, tool-loop limits and invalid calls, the exact MCP tool set, and unavailable-client HTTP behavior. The chat router maps only `LLM_UNAVAILABLE` to the planned static 503 response because the shared API mapper does not currently map that code.
**Tasks In Progress**: None in p27 scope; formal review and combined-tree integration belong to downstream workflow steps.
**Blockers**: None for p27 scoped implementation. Full-tree typelint remains nonzero on other plan paths; p27 paths are clean.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/assistant src/telescreen/api/routers/chat.py tests/assistant` — exit 0; `/tmp/telescreen-p27-assistant-format-final.log`.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/assistant src/telescreen/api/routers/chat.py tests/assistant` — exit 0; `/tmp/telescreen-p27-assistant-ruff-final.log`.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/assistant src/telescreen/api/routers/chat.py tests/assistant` — exit 0; `/tmp/telescreen-p27-assistant-ty-final.log`.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/assistant` — exit 0; 6 passed, 0 failed; `/tmp/telescreen-p27-assistant-pytest-final.log`.
- `uv run --frozen python tools/typelint.py` — full tree exit 1 on 93 diagnostics outside p27; absolute-path filter found no p27 diagnostics; `/tmp/telescreen-p27-assistant-typelint-final.log`.
**Source SHA-256**: `src/telescreen/assistant/__init__.py` 7a1643a55e6ec14d72df7cec1c76ed612b89a99cd54f6224aa189ca69648ff1a; `chat.py` e4de1c3ce8628bbfcd3a1f698293183c20282b6f0297e3ca2182b6c107fba7a2; `mcp_server.py` 9fdb5ad177388a3b248e993407886b139b54c94e105a3401ff7e0e56172843b8; `tools.py` 4c4e3bf2204eb641ab7e062c6167accdec2b64e0510e8d9888fb97cd3ddb9b7f; `src/telescreen/api/routers/chat.py` 5e6cb5c5f62397fbc84ff8662c45b8abbfc381d9b8f9576761d670c31509cb78; `tests/assistant/test_assistant.py` 98da33d0b4b2e8d50936fe0dddf11f46e02f704d0938a5527b8d3ea2d7b40eb9.
