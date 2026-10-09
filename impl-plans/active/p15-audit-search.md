# P15 Audit Log and Search Implementation Plan

**Status**: Completed (implementation; formal review pending)
**Design Reference**: design-docs/specs/design-data-model.md (Audit), design-docs/specs/design-api-and-ui.md (Search, Endpoints: Audit, Search), design-docs/specs/design-masking.md (IndexGuard), design-docs/specs/architecture.md section 5 item 8
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

## Plan Metadata

- planId: `p15-audit-search`
- planPath: `impl-plans/active/p15-audit-search.md`
- wave: 4
- dependsOn: `p08-masking`, `p10-api-common`
- writePaths:
  - `impl-plans/active/p15-audit-search.md`
  - `src/telescreen/audit`
  - `src/telescreen/search`
  - `src/telescreen/api/routers/audit.py`
  - `src/telescreen/api/routers/search.py`
  - `tests/audit`
  - `tests/search`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

This plan covers two acceptance items: "audit log is append-only, hash-chained and
searchable" and "Meilisearch indexing goes through an IndexGuard". It implements the
`AuditLog` and `SearchIndexer` ports used by every other feature package, the
Meilisearch backend (httpx, no vendor SDK), an in-memory backend for dev and tests, and
the audit and search APIs.

## Non-goals

- No outbox table (design decision). Index failures are logged and repaired by reindex.
- Do not write tables other than `audit_records` and `audit_chain_head`. Other tables are
  read-only here, for building documents.

## Deliverables

### `src/telescreen/audit/`
- `chain.py`: `GENESIS_HASH = "0" * 64`;
  `compute_hash(prev_hash: str, seq: int, record_id: str, at: datetime, actor_kind: str, actor_ref: str, action: str, target_kind: str, target_id: str, summary: str, details_json: str) -> str`.
  The value is `sha256(prev_hash + "\n" + dump_json(object of those fields))`, with `at`
  written as `ensure_utc(at).isoformat()`.
- `service.py`: `AuditLogService(clock: Clock, redactor: Redactor)` implements `AuditLog`.
  `append`:
  - locks `audit_chain_head` with `with_for_update()`;
  - sets seq = head.seq + 1;
  - redacts `summary` and every string inside `details` (recursively over `JsonValue`);
  - inserts the row and updates the head;
  - returns the seq.
  It never updates or deletes rows.
- `verify.py`: `ChainVerification(ok: bool, first_broken_seq: int | None, checked: int)`;
  `async def verify_chain(session) -> ChainVerification` recomputes the chain in pages of
  1000, ordered by seq, and checks the head too.
- `query.py`: `AuditQuery(q: str, action: str | None, actor: str | None, target_kind: str | None, start: datetime | None, end: datetime | None, limit: int, offset: int)`
  and `search_audit(backend: SearchBackend, query) -> AuditSearchPage`, which goes
  through the `audit` index.

### `src/telescreen/search/`
- `backend.py`:
  - `type FieldValue = str | int | float | bool | None`, a non-recursive PEP 695 alias
    (a plain assignment fails TL003); fields may also hold `tuple[str, ...]`;
  - `SearchDocument(id: str, fields: tuple[tuple[str, FieldValue | tuple[str, ...]], ...])`;
  - `FilterOp` (eq, gte, lte); `SearchFilter(field: str, op: FilterOp, value: str | int)`;
  - `SearchQuery(q: str, filters: tuple[SearchFilter, ...] = (), sort: tuple[str, ...] = (), limit: int = 20, offset: int = 0)`;
  - `SearchPage(hits: tuple[SearchDocument, ...], total: int)`;
  - `IndexName` StrEnum: issues, findings, runs, audit;
  - Protocol `SearchBackend`: `ensure_indexes()`, `upsert(index, documents)`,
    `delete(index, ids)`, `search(index, query) -> SearchPage`.
- `settings.py`: the index settings table. `issues`: filterable status, source,
  rule_class, severity, assignee; sortable last_seen_ts, first_seen_ts, times_seen.
  `findings`: filterable issue_id, rule_class. `runs`: filterable status, workflow,
  issue_id. `audit`: filterable action, actor_ref, target_kind, at_ts; sortable seq,
  at_ts.
- `meili.py`: `MeiliSearchBackend(http: httpx.AsyncClient, base_url: str, api_key: SecretValue | None)`.
  - Uses `PUT /indexes/{uid}/settings` (create the index first with `POST /indexes`;
    409 means it already exists), `POST /indexes/{uid}/documents` (primary key `id`),
    `POST /indexes/{uid}/documents/delete-batch` and `POST /indexes/{uid}/search` with
    `filter` and `sort` arrays.
  - Bodies are built as `JsonValue` and sent with `content=dump_json(...)`.
  - Responses are parsed with `TypeAdapter(...).validate_json`; hits are converted into
    `SearchDocument` through `parse_json` accessors.
  - Filter strings escape `"` and `\`.
- `memory.py`: `InMemorySearchBackend` with case-insensitive substring search over string
  fields, filters and sort. It is not durable.
- `documents.py`: builders from ORM rows:
  - issue: id, short_id, number, display_title, status, source, rule_class, severity,
    display_locator, assignee (member id, else team id, else null), times_seen,
    first_seen_ts, last_seen_ts;
  - finding: id, issue_id, rule_id, rule_class, masked_snippet, location,
    observed_at_ts;
  - run: id, workflow, status, issue_id, started_at_ts;
  - audit: id, seq, at, at_ts, actor_kind, actor_ref, action, target_kind, target_id,
    summary.
  `*_ts` fields are integer epoch seconds.
- `indexer.py`: `DocumentIndexer(sessions, backend: SearchBackend, guard: IndexGuard, audit: AuditLog, clock: Clock)`
  implements `SearchIndexer` (`index_issue`, `index_finding`, `index_run`). Each method
  loads the row (own session), builds the document and runs
  `guard.check(document string fields)`.
  - On rejection: no upsert; an audit entry `index_guard.rejected` with details index,
    document id, field names and rule ids (own transaction); a masked warning log.
  - On success: upsert.
  - Every exception is caught and logged with `logger.warning("index failed", extra=...)`
    with ids only. It never raises.
- `audit_tick.py`: `AuditIndexSchedule(sessions, backend, guard, audit_log)` implements
  `ScheduleProvider`. Each tick indexes up to 500 audit records with seq > watermark,
  through the guard. The watermark is kept in memory and initialized from the backend's
  highest indexed seq (a search sorted by `seq:desc`, limit 1).
- `reindex.py`: `async def reindex_all(sessions, backend, guard, audit_log) -> ReindexCounts`
  pages through all issues, findings, runs and audit records (1000 per page) and indexes
  them through the guard.

### Routers
- `routers/audit.py`: `AuditRouterDeps(ctx: ApiContext, backend: SearchBackend)`;
  `GET /api/audit/search?q&action&actor&target_kind&from&to&limit&offset` (read) ->
  `{items: AuditRecord[], total}`; `GET /api/audit/verify` (read) ->
  `{ok, first_broken_seq, checked}`.
- `routers/search.py`: `SearchRouterDeps(ctx, backend)`;
  `GET /api/search?q&index=issues|findings|runs|audit&limit` (read) -> `{items: SearchHit[], total}`.
  `SearchHit(index, id, title, snippet, status, at)` is mapped per index:
  - issues: title `short_id + " " + display_title`, snippet = display_locator;
  - findings: title = rule_id, snippet = masked_snippet;
  - runs: title = workflow, snippet = issue_id;
  - audit: title = action, snippet = summary.

## Key Pitfalls

- Chain race: the head row lock serializes appends on PostgreSQL. In SQLite tests run
  appends sequentially.
- `at` precision: store the exact datetime used in the hash. Re-read values through
  `ensure_utc` before recomputing.
- Never pass raw text to the backend. Everything is built from masked columns AND checked
  by the guard.
- The guard checks every string field, including tuple elements.

## Tests

- `tests/audit/`:
  - 3 appends -> seq 1, 2, 3; `verify_chain` ok.
  - Tamper with the stored summary of seq 2 through raw SQL -> `first_broken_seq == 2`.
  - An append with a sample secret in the summary and in a nested details string -> the
    stored row has no leak.
  - The audit API search returns records from an in-memory backend filled by
    `AuditIndexSchedule`.
  - `@pytest.mark.integration`: on PostgreSQL an UPDATE of `audit_records` raises (the
    trigger from p02).
- `tests/search/`:
  - The indexer with a guard and an in-memory backend: an issue row whose
    `display_title` contains a sample (inserted directly) -> not indexed, an audit
    `index_guard.rejected` entry exists, and the details hold no leak; a clean issue ->
    indexed and searchable by `short_id`.
  - Meili backend with MockTransport: the settings call body has the filterable
    attributes; search sends the filter `status = "resolved"` correctly escaped; a 500 on
    upsert -> the indexer logs and does not raise.
  - `reindex_all` counts.
  - The search API maps hits per index.
  - `@pytest.mark.integration` (`TELESCREEN_TEST_MEILI_URL`): ensure, upsert, then search
    round trip.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen/audit src/telescreen/search src/telescreen/api/routers/audit.py src/telescreen/api/routers/search.py tests/audit tests/search"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/audit tests/search 2>&1 | tee /tmp/telescreen-p15-audit-search-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p15-audit-search-typelint.log 2>&1; grep -E '^(src/telescreen/(audit|search)|src/telescreen/api/routers/(audit|search)\.py|tests/(audit|search))' /tmp/telescreen-p15-audit-search-typelint.log   # prints nothing
```

## Completion Criteria

- [x] AuditLogService, verify_chain, the search backends, DocumentIndexer, AuditIndexSchedule, reindex_all and both routers delivered with the exact names
- [x] Guard rejection, tamper detection and leak tests pass
- [x] Audit API search and verify are exercised through an authenticated in-process ASGI app, including action, actor, from/to, response mapping, and missing-token assertions
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 implementation
**Tasks Completed**: Implemented append-only hash-chained audit storage and verification, audit search, guarded issue/finding/run/audit indexing, Meilisearch and in-memory backends, incremental audit indexing, full reindexing, and read-scoped audit/search routers. Added redaction, tamper, guard rejection, safe logging, backend escaping, reindex, schedule, and per-index SearchHit mapping tests. Audit index lookup uses the unique record id column while preserving the sequence primary key.
**Tasks In Progress**: Formal test-integrity, adversarial, and combined-tree integration reviews are owned by downstream workflow steps.
**Blockers**: None for p15 scoped implementation.
**Verification**: `ruff format --check`, `ruff check`, `ty check`, and `pytest tests/audit tests/search` all exited 0; pytest reported 8 passed, 0 failed. Full-tree `tools/typelint.py` exited 1 on diagnostics outside p15; the correctly unanchored absolute-path filter reported no p15 diagnostics. Logs: `/tmp/telescreen-p15-format-final-v2.log`, `/tmp/telescreen-p15-ruff-final-v2.log`, `/tmp/telescreen-p15-ty-final-v2.log`, `/tmp/telescreen-p15-pytest-final-v2.log`, `/tmp/telescreen-p15-typelint-full-final-v2.log`, `/tmp/telescreen-p15-typelint-scoped-final-v2.log`.

### Session: 2026-10-09 review repair (P15-TI-AUDIT-API-UNTESTED)
**Tasks Completed**: Added `test_audit_api_search_and_verify_use_indexed_records` in `tests/audit/test_audit.py`. The test creates a READ bearer, appends two distinct member actions, indexes the three resulting records through `AuditIndexSchedule` into `InMemorySearchBackend`, and exercises `/api/audit/search` action/actor filters, timezone-aware `from`/`to` windows, mapped item fields, `/api/audit/verify`, and unauthenticated 401 behavior. The eight earlier tests remain in place.
**Tasks In Progress**: Independent test-integrity and adversarial re-review are owned by downstream workflow steps.
**Blockers**: None for the p15 implementation repair.
**Verification**: The focused API test passed (1 passed). Final scoped gates passed: ruff format, ruff check, ty check, typelint (18 files clean), and `pytest tests/audit tests/search` (9 passed, 0 failed). Final-source logs: `/tmp/telescreen-p15-audit-search-format-final-comm005509-v4.log`, `/tmp/telescreen-p15-audit-search-ruff-final-comm005509-v4.log`, `/tmp/telescreen-p15-audit-search-ty-final-comm005509-v4.log`, `/tmp/telescreen-p15-audit-search-typelint-scoped-final-comm005509-v4.log`, `/tmp/telescreen-p15-audit-search-pytest-final-comm005509-v4.log`. Earlier format/type diagnostics and a SQLite-naive-timestamp test assertion were corrected before these final gates; the record mapping assertion compares UTC-normalized timestamps to the persisted audit row.
