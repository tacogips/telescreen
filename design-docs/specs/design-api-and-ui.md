# Reporting Bureau API, Assistant and UI

REST API surface, authentication, error model, evidence API, search, Assistant MCP/chat,
and the SolidJS UI.

## Overview

The Bureau is a FastAPI app built by `telescreen.api.app.create_app(container)`. Routers are
owned by feature packages (`telescreen.api.routers.<area>`); shared auth, error handling and
pagination live in `telescreen.api.common`. All bodies are pydantic dataclasses. Responses
contain only masked data. The UI consumes the same API.

## Details

### Authentication and errors (`telescreen.api.common`)

- `Authorization: Bearer <token>`; `require_scope(scope)` dependency resolves the token hash
  in `api_tokens` and yields `Principal(token_id, member_id, scopes)`. `admin` implies all.
- Webhook endpoints (`/api/collectors/*`) authenticate by provider signatures; police endpoints by
  enrollment/worker tokens or push signatures.
- Error body: `{"error": {"code": "<snake_case>", "detail": "<static text>", "error_id"?}}`.
  Codes include `validation_error`, `unauthorized`, `forbidden`, `not_found`, `conflict`,
  `raw_value_detected`, `evidence_rejected`, `evidence_required`, `unsupported_media_type`,
  `evidence_scanner_unavailable`,
  `payload_too_large`, `invalid_transition`, `internal_error`.
- Validation errors list only `loc` and `type` (see Masking design).
- Pagination: `?limit=<1-200>&cursor=<opaque>`; responses `{items, next_cursor}`.

### Free-form JSON on the wire

`JsonValue` (`telescreen.core.json`) is an internal model. Its dataclass shape (kind, items,
members) must never appear on the wire, so pydantic dataclass bodies never have a
`JsonValue` field. Two rules apply:

| API family | Free-form members | Encoding |
|------------|-------------------|----------|
| Internal REST API used by the UI (`/api/collectors`, `/api/runs`) | collector `selector`, step `input`/`output`, run `context` (escalation tiers and workflow sources are typed or plain strings and are not affected) | A JSON-encoded string field with the `_json` suffix (`selector_json`, `input_json`, `output_json`, `context_json`), the same canonical text as the DB column. Inbound strings are parsed with `parse_json` and decoded into the per-kind dataclasses; invalid documents return 422 `validation_error` with JSON paths only. The UI uses `JSON.parse`/`JSON.stringify` |
| External contracts (`/api/police/v1`, `GET /api/ingest/v1/sources/{name}/config`) | police task `input`, result `payload`, custom selector `document` | Native JSON objects, so n8n and other clients need no double decoding. The router reads the raw body. Typed members are parsed with `TypeAdapter.validate_json` on a dataclass without the free-form member (unknown members are ignored). The free-form member is extracted with `parse_json` and its accessors. Responses are written with `dump_json` into a `Response` with type `application/json`. The SDK does the same through `telescreen_police_sdk/_json.py` |

### Endpoints

| Area | Endpoint | Scope |
|------|----------|-------|
| Health | `GET /api/health` | none |
| Ingest | `POST /api/ingest/v1/scan`, `POST /api/ingest/v1/findings`, `GET /api/ingest/v1/sources/{name}/config` | ingest |
| Issues | `GET /api/issues?status&source&rule_class&severity&assignee&q&sort=last_seen|first_seen|times_seen` | read |
| | `GET /api/issues/{id}`, `/events`, `/history`, `/stats` (linked runs come from `GET /api/runs?issue_id=`, evidence from `GET /api/evidence?issue_id=`, so each router reads only its own package's tables) | read |
| | `PATCH /api/issues/{id}` `{status?, assignee_ref?}`; `POST /api/issues/{id}/comments` | write |
| Workflows | `GET /api/workflows`, `GET /api/workflows/{name}`, `GET /api/workflows/{name}/versions/{v}`, `GET /api/workflows/schema` | read |
| | `POST /api/workflows` `{source_format, source_text}` (new version), `POST /api/workflows/validate`, `POST /api/workflows/{name}/activate {version}` | write |
| Runs | `POST /api/workflows/{name}/runs {issue_id?}`, `GET /api/runs?status&workflow&issue_id`, `GET /api/runs/{id}` (steps, transitions, human tasks), `POST /api/runs/{id}/cancel` | read / write |
| Human tasks | `GET /api/human-tasks?assignee=me|<ref>&status`, `POST /api/human-tasks/{id}/complete {outcome, comment?}` | read / write |
| Evidence | `PUT /api/evidence?issue_id&step_run_id?&filename` (raw body), `GET /api/evidence?issue_id&step_run_id?`, `GET /api/evidence/{id}`, `GET /api/evidence/{id}/content` | write / read |
| Escalation | `GET/POST /api/escalation-policies`, `PUT /api/escalation-policies/{name}` | read / admin |
| Directory | `GET/POST /api/members`, `GET/POST /api/teams`, `PUT /api/teams/{slug}/members` | read / admin |
| Collectors | `GET /api/collectors`, `POST /api/collectors`, `PATCH /api/collectors/{id}`, `POST /api/collectors/{id}/run`, `POST /api/collectors/{id}/sweep` (via `SweepLauncher`; 409 `conflict` while a sweep of the config is active) | read / admin |
| Map jobs | `GET /api/map-jobs?origin&status&collector_config_id`, `GET /api/map-jobs/{id}` (counts by status, partitions done/total, findings so far, paused_until, failed shard keys with error codes) | read |
| | `POST /api/map-jobs/{id}/cancel`, `POST /api/map-jobs/{id}/retry-failed` | admin |
| Collector webhooks | `POST /api/collectors/{kind}/webhook` for `slack`, `github`, `gdrive` (also serves gdocs/gsheets), dispatched through `CollectorRegistry` | signatures |
| Police | `/api/police/v1/*` (contract), `GET /api/police/workers`, `POST /api/police/workers/{id}/disable` | contract / read, admin |
| Audit | `GET /api/audit/search?q&action&actor&target_kind&from&to`, `GET /api/audit/verify` | read |
| Search | `GET /api/search?q&index=issues|findings|runs|audit` | read |
| Chat | `POST /api/chat {messages: [{role, content}]}` | read |

### Evidence

- Upload is a raw request body (`application/octet-stream`), read in memory up to
  `TELESCREEN_EVIDENCE_MAX_BYTES` (default 10 MiB); no multipart, so no temporary spool
  files hold unscanned content.
- Accepted content types (user decision, `design-docs/user-qa/qa-evidence-file-types.md`):
  - Text-like, detected from the filename extension and UTF-8 decodability: `.txt`, `.md`,
    `.log`, `.csv`, `.json`, `.yaml`, `.yml`. Scanned with the configured text `Detector`.
  - Images: `.png`, `.jpg`/`.jpeg`, `.webp`, verified by magic bytes (extension and magic
    must agree). Scanned by an `ImageEvidenceScanner` Protocol whose default implementation
    sends the image to the vision-capable managed LLM (`TELESCREEN_MANAGED_LLM_PROVIDER`,
    default `claude`; see design-detectors.md). The model returns only
    `{contains_sensitive: bool, rule_classes: [...]}` and is instructed never to transcribe
    content; free text in the response rejects the scan. Images cannot be masked, so the
    verdict is accept or reject only. EXIF/metadata (GPS, author) is stripped before
    storage. Fail closed: when no image scanner is configured (no managed LLM credentials),
    image uploads are rejected with 503 `evidence_scanner_unavailable`; scanner errors
    after retries also reject.
  - Anything else is rejected with 415 `unsupported_media_type`.
- Scan with the configured detector (injected `Detector` for text, `ImageEvidenceScanner` for
  images); any finding rejects: content is
  discarded, an `evidence` row with verdict `rejected` and the rule classes is stored, 422
  `evidence_rejected` with rule classes only, audit record.
- Clean content is stored through `EvidenceStore` (`LocalFileEvidenceStore` at
  `TELESCREEN_EVIDENCE_DIR`, key = evidence id) with sha256; the filename is redacted;
  history `evidence_added` is written through `IssueRecorder.add_history`; `RunSignaler` is
  not called (completion is explicit through the human task).

### Search

- `SearchBackend` port: `ensure_indexes()`, `upsert(index, documents)`, `delete(index,
  ids)`, `search(index, query, filters, limit, offset) -> SearchPage`.
- `MeiliSearchBackend` (httpx, `TELESCREEN_MEILI_URL`, key secret ref) with index settings:
  `issues` (filterable: status, source, rule_class, severity, assignee; sortable: last_seen,
  first_seen, times_seen), `findings` (filterable: issue_id, rule_class), `runs`
  (filterable: status, workflow, issue_id), `audit` (filterable: action, actor_ref,
  target_kind, at; sortable: seq, at). `InMemorySearchBackend` for dev/tests.
- `DocumentIndexer` implements `SearchIndexer`: builds documents from masked DB rows, runs
  `IndexGuard`, then upserts. `telescreen search reindex` rebuilds all indexes.

### Assistant (`telescreen.assistant`)

- `AssistantTools` service with typed methods: `search_issues(query, status, limit)`,
  `get_issue(issue_id)`, `count_incidents(start, end, group_by: status|source|rule_class|
  severity)`, `incident_report(start, end)` (markdown summary), `list_workflow_runs(status,
  limit)`, `audit_search(query, limit)`. Outputs are JSON or markdown text passed through
  `Redactor` as a final guard.
- MCP server: `telescreen mcp` runs the `mcp` SDK `FastMCP` over stdio with these tools.
- Chat: `POST /api/chat` runs a tool loop on the managed LLM provider selected by
  `TELESCREEN_MANAGED_LLM_PROVIDER` (`claude` default, or `codex`). It uses the shared
  `LlmClient` from `telescreen.detection.factory` and the model from
  `TELESCREEN_MANAGED_LLM_MODEL`.
  - The loop is provider-agnostic and does not use provider-native tool calling. On each
    round the model returns JSON matching the `ChatStep` schema: either
    `{action: "tool", tool, arguments}` or `{action: "answer", answer}`.
  - At most 6 tool rounds.
  - User messages are redacted before sending.
  - Tool arguments are parsed into per-tool dataclasses with `TypeAdapter`.
  - Tool results (already masked) are appended to the transcript.
  - The final answer is redacted.
  - No chat history is stored; the client resends the transcript.
  - Tests use a fake `LlmClient`.

### UI (`src/web`)

Visual design, layout, components and page patterns follow
`design-docs/specs/design-ui-sentry.md` (Sentry-based UI, owner decision 2026-10-09). That
document takes precedence over the page descriptions below where they differ.

Stack: SolidJS, TypeScript (strict), Vite, `@solidjs/router`, vitest, jsdom,
`@solidjs/testing-library`. Package scripts (owned by foundation in `package.json`):
`dev`, `build` (`vite build`), `typecheck` (`tsc --noEmit`), `test` (`vitest run`).

- Shell: Sentry-like sidebar (Issues, Search, Workflows, Runs, Police, Collectors, Escalation
  Policies, Audit, Assistant, Settings), header with environment label, route table in
  `routes.tsx` pointing to `pages/<area>/index.tsx`.
- API client: `src/api/client.ts` (fetch wrapper with bearer token, error decoding into the
  error model) and `src/api/types.ts` (hand-written types mirroring the API dataclasses).
  The token is entered on the Settings page and kept in `sessionStorage`.
- Pages:
  - Issues list: filters (status, source, rule class, severity, assignee, free text via
    search), columns short id, title, status badge, first seen, last seen, events, 14-day
    sparkline (inline SVG).
  - Issue detail: masked event list, history timeline, assignment, status transition
    buttons (allowed transitions only), linked runs with current step, evidence list and
    upload with rejection messages.
  - Search: global search across indexes.
  - Workflows: list, YAML/JSON editor (textarea with server-side validate and error list),
    version list, activate.
  - Runs: list and detail with ordered steps, statuses, visits, send-back transitions,
    waiting human tasks and SLA due times, human task completion form.
  - Police (workers, mode, labels, heartbeat age), Collectors (configs, trigger mode, run now),
    Escalation Policies (tier editor as JSON), Audit (search and chain verify), Assistant
    chat, Settings.
- Tests: component tests per page with a mocked fetch.
- Serving: `vite` dev server proxies `/api` to `http://localhost:8000`; in production the
  Bureau serves `TELESCREEN_WEB_DIST` as static files at `/`.

## References

See `design-docs/references/README.md`.
