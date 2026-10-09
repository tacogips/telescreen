# P16 Issues (Grouping, Lifecycle, Scan Pipeline) and Push Ingestion Implementation Plan

**Status**: In Progress (run A resume, session 338)
**Design Reference**: design-docs/specs/design-collectors-and-ingestion.md (Scan pipeline, Issue grouping and lifecycle, Resource identity, Push ingestion API), design-docs/specs/design-data-model.md (Findings and issues, Keys, Issue status machine), design-docs/specs/design-map-reduce-sweeps.md (Reduce: resolve_unchecked)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

## Plan Metadata

- planId: `p16-issues-ingest`
- planPath: `impl-plans/active/p16-issues-ingest.md`
- wave: 4
- dependsOn: `p08-masking`, `p10-api-common`
- writePaths:
  - `impl-plans/active/p16-issues-ingest.md`
  - `src/telescreen/issues`
  - `src/telescreen/api/routers/issues.py`
  - `src/telescreen/api/routers/ingest.py`
  - `tests/issues`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

This is the Sentry core. Masked findings are grouped into issues by
`(source, resource_key, rule_class)` and carry first and last seen, times_seen, status,
regression, auto-resolve, history and audit. The package implements these ports:

- `IssueRecorder`, `IssueReader` and `ResourceScanner`;
- the push ingestion API (custom collectors push content to scan transiently, or push
  already-masked findings that are re-checked);
- the issues REST API.

## Non-goals

- No workflow starts here: emit `IssueEvent`s through the injected `IssueEventSink`.
- No collector configs or scan-job writes: use the `CollectorConfigReader` and
  `ScanJobStore` ports.
- No search engine use: `q` filters with SQL `LIKE` over `display_title` and
  `display_locator`.

## Deliverables

### `src/telescreen/issues/`
- `keys.py`: `grouping_key(source: str, resource_key: str, rule_class: RuleClass) -> str`
  (`sha256(source + "\0" + resource_key + "\0" + rule_class)` hex); `short_id(number: int) -> str`
  (`TELE-<n>`).
- `transitions.py`: `USER_TRANSITIONS` (a tuple of (from, allowed tuple) pairs exactly as
  the data-model table) and `ensure_user_transition(current, target) -> None` (raises
  `invalid_transition`).
- `repository.py`: queries on `IssueRow`, `FindingRow`, `IssueHistoryRow` and
  `CounterRow`. `next_issue_number(session)` reads and updates the `issue_number`
  counter with `with_for_update()`.
- `service.py`: `IssueService(audit: AuditLog, events: IssueEventSink, fingerprinter: Fingerprinter, redactor: Redactor, resolver: RecipientResolver, clock: Clock)`
  implements `IssueRecorder` and `IssueReader`, plus `change_status(session, issue_id, target: IssueStatus, actor_ref: str) -> IssueRow`,
  `assign(session, issue_id, assignee_ref: str, actor_ref) -> IssueRow` and
  `comment(session, issue_id, body: str, actor_ref) -> None`.
  - `record_scan(session, scan)`:
    1. Group the findings by rule class.
    2. Per class, get or create the issue (`grouping_key`). New issues get status
       `unresolved`, `first_seen = last_seen = observed_at`, history `created`, audit
       `issue.created` and event `issue_created`.
    3. Insert findings rows. A duplicate (issue_id, scan_job_id, fingerprint) is skipped:
       use a `session.begin_nested()` savepoint and catch `IntegrityError`.
    4. `times_seen += inserted`; `last_seen = observed_at` when anything was inserted;
       `distinct_fingerprints` is recounted.
    5. A `resolved` issue with an inserted finding becomes `regressed`:
       `regression_count += 1`, history `regressed`, audit and event `issue_regressed`.
       An `ignored` issue only gets counter updates.
    6. If `scan.complete`: every open issue (unresolved, acknowledged, in_progress,
       regressed) on the same `resource_key` whose rule class is absent becomes
       `resolved` with `resolution=auto` and `resolved_at`, history `auto_resolved`,
       audit `issue.auto_resolved`.
    7. EVERY issue on the `resource_key` (any status) gets
       `last_checked_at = observed_at` and `sweep_covered = scan.sweep_covered`.
    8. Return a `RecordOutcome`. Its `issue_ids` are the issues that have at least one
       finding in THIS scan, whether inserted now or already present. The rescanner
       (p20) relies on this to decide found or clean.
  - `record_external(session, scan)`:
    1. Run `Redactor.has_hits` on every string field of every finding and locator; any
       hit raises `TelescreenError(raw_value_detected)` (static detail).
    2. Per locator: `raw_locator = scan.source + ":" + locator`; `resource_key` and
       `display_locator` come from the fingerprinter and redactor;
       `fingerprint = fingerprinter.external(source, external_fingerprint)`;
       `severity = severity_for(rule_class)`.
    3. Call `record_scan` per locator, with `complete = locator in complete_locators`.
       Each `complete_locators` entry without findings becomes a complete empty scan.
  - `add_history`: inserts an `IssueHistoryRow` with the note redacted.
  - `resolve_unchecked(session, collector_config_id, checked_before)`: open issues with
    `sweep_covered` true and `last_checked_at < checked_before` are auto-resolved (as
    above). Returns the count.
  - `get` returns an `IssueSnapshot` (`short_id`, `ref_json` = `resource_ref_json`).
    `open_resource_refs` covers open issues of the config, one entry per distinct
    `resource_key`, including `sweep_covered`.
- `scan.py`: `ResourceScannerImpl(sessions, detector: Detector, detector_factory: Callable[[tuple[DetectorKind, ...]], Detector], masker: Masker, fingerprinter: Fingerprinter, redactor: Redactor, recorder: IssueService, indexer: SearchIndexer, clock: Clock, batch_items: int, max_bytes: int)`
  implements `ResourceScanner`. An explicit `detectors` argument uses
  `detector_factory(detectors)` (cache by tuple); `None` uses `detector`. Assembly binds
  the factory to `build_detector`. The steps are:
  1. If the total UTF-8 bytes of the segments exceed `max_bytes`, record an INCOMPLETE
     scan with no findings (this updates `last_checked_at` only) and return an outcome
     with `skipped=True`.
  2. Otherwise build `ScanItem`s per segment (`locator` = segment locator), call
     `detector.detect_batch` in chunks of `batch_items`, and `masker.seal` each result.
  3. Build the `ResourceScan`:
     - `resource_key` comes from `raw_locator`;
     - `display_locator` is the redacted `raw_locator`;
     - `display_title` is the redacted title;
     - `ref_json` is stored only when `redactor.has_hits(ref_json)` is false.
  4. In ONE transaction call `record_scan`. After commit, index the affected issues and
     the new findings via `SearchIndexer`.
  5. The content object is not stored anywhere.
- `ingest.py`: `IngestService(sessions, scanner: ResourceScanner, recorder: IssueService, configs: CollectorConfigReader, scan_jobs: ScanJobStore, indexer: SearchIndexer, clock)`:
  - `scan_push(source, resources) -> IngestScanResult(scan_job_id, resources, findings, issue_ids)`;
  - `findings_push(source, locator, complete, findings) -> RecordOutcome`.
  - `source` must be `custom:<name>`, where `<name>` is an enabled `custom` collector
    config; otherwise `unknown_source` (404).
  - Push scans use `sweep_covered=False`. Scan jobs use trigger `push`.

### Routers
- `routers/issues.py`: `IssuesRouterDeps(ctx: ApiContext, service: IssueService, indexer: SearchIndexer)`.
  - `GET /api/issues?status&source&rule_class&severity&assignee&q&sort&limit&cursor`
    (read; sort is one of last_seen, first_seen, times_seen, descending; keyset cursor).
    `assignee` is a ref; resolve it with `RecipientResolver.assignee_of`.
  - `GET /api/issues/{id}`, `/events` (findings, paged), `/history`.
  - `/stats?bucket=day|hour&days=14` -> `{buckets: [{start, count}]}`, zero-filled.
  - `PATCH /api/issues/{id}` with `{status?, assignee_ref?}` (write).
  - `POST /api/issues/{id}/comments` with `{body}` (write; redacted).
  - Responses use the data-model column names plus `short_id`.
- `routers/ingest.py`: `IngestRouterDeps(ctx, ingest: IngestService, configs: CollectorConfigReader, max_scan_bytes: int = 10485760)`.
  - `POST /api/ingest/v1/scan` (ingest scope): reads the raw body with
    `read_limited_body`; parses it with `TypeAdapter(IngestScanBody).validate_json`,
    where the text fields of the body dataclasses are `repr=False`; a parse failure ->
    `validation_error` with a static detail.
  - `POST /api/ingest/v1/findings`.
  - `GET /api/ingest/v1/sources/{name}/config`: the selector `document` as native JSON
    via `dump_json` in a `Response(media_type="application/json")`.

## Key Pitfalls

- Never put raw locators, titles or segment text in logs, errors, audit details or
  history notes. Audit details carry ids and counts only.
- Counter race: lock the counter row (`with_for_update`) inside the same transaction that
  inserts the issue.
- Issue events must be emitted inside the same transaction (pass the session to
  `on_issue_event`), so workflow starts commit atomically with the issue.
- `ref_json` that contains a detectable value is dropped (stored as null), not masked.
- SQLite returns naive datetimes: normalize with `ensure_utc` before comparing.

## Tests (`tests/issues/`)

Use the SQLite harness, a recording fake `IssueEventSink`, a fake `AuditLog`, a fake
`SearchIndexer`, a fake config reader, a fake scan-job store, the real `RuleDetector`,
`Masker` and `Redactor`, and `FakeClock`.

- First scan of a resource with two emails and one token -> 2 issues (email, token);
  email `times_seen=2`, `distinct_fingerprints=2`; events `issue_created` x2;
  `TELE-1`, `TELE-2`.
- Same scan again with the same scan job id -> no new findings; with a new scan job ->
  `times_seen` grows and `last_seen` moves.
- A complete scan without the token -> the token issue becomes resolved/auto with history
  `auto_resolved`; then a new token finding -> `regressed`, `regression_count=1`, event
  `issue_regressed`.
- An ignored issue with a new finding -> counters only, no event.
- A resource over `max_bytes` -> `skipped=True`; the existing issue's `last_checked_at`
  is updated and its status unchanged.
- `resolve_unchecked`: an open covered issue with an old `last_checked_at` -> resolved;
  `sweep_covered=False` -> untouched; a recent issue -> untouched.
- `record_external` with a raw sample in `masked_snippet` -> `raw_value_detected`,
  nothing persisted; clean findings -> fingerprints re-keyed (not equal to the external
  value).
- User transitions: allowed -> history and audit; disallowed -> 409.
- API: the list filters and sort, stats zero-fill, and comments are redacted.
- Ingest: scan push with an unknown source -> 404; with samples -> issues created, and
  no sample appears in any DB text column (scan all rows of issues, findings and
  issue_history with `find_leaks`) or in the response. Findings push with a raw value ->
  422 with no echo. The config endpoint returns the native JSON document.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/issues src/telescreen/api/routers/issues.py src/telescreen/api/routers/ingest.py tests/issues
uv run --frozen ruff check --no-cache src/telescreen/issues src/telescreen/api/routers/issues.py src/telescreen/api/routers/ingest.py tests/issues
uv run --frozen ty check src/telescreen/issues src/telescreen/api/routers/issues.py src/telescreen/api/routers/ingest.py tests/issues
uv run --frozen pytest -p no:cacheprovider tests/issues 2>&1 | tee /tmp/telescreen-p16-issues-ingest-pytest.log
uv run --frozen python tools/typelint.py src/telescreen/issues src/telescreen/api/routers/issues.py src/telescreen/api/routers/ingest.py tests/issues
```

## Run A Resume (2026-10-09, session 338)

State: s319 recorded p16 as BLOCKED, but no cause is written in-tree. Dispatch:
`impl-plans/active/telescreen-v1-20261009-s338-dispatch.json`, wave A1. Resume from the
in-tree code; keep every public name. Treat any failing gate in R0 as the probable block
cause and fix it first. `tests/issues/test_issues.py` is 884 lines, so put new tests in
`tests/issues/test_issues_api.py` (and further files if needed). Static gap review (s338
planning) found:

- [ ] R0 Baseline: run the Verification block on the untouched tree; log exit codes to
  `/tmp/telescreen-p16-issues-ingest-baseline-*.log` before any edit.
- [ ] R1 Audit actor: `issues/service.py:_audit` records every entry as
  `ActorKind.SYSTEM`/`system:issues`. For `change_status`, assignment and comments, pass the
  caller's `actor_ref` and actor kind so the audit trail shows who acted (requirement 11).
  Scan-driven events stay `system`. Test: PATCH by a user token -> the audit row actor is
  that user ref.
- [ ] R2 `routers/ingest.py` findings endpoint: read the raw body with
  `read_limited_body` and parse with `TypeAdapter(...).validate_json`, the same as the scan
  endpoint (bounded body, static `validation_error`, no echo). Test: an oversized body ->
  413; a malformed body -> 422 whose response holds no request text.
- [ ] R3 `issues/ingest.py:IngestService.scan_push`: if the scanner raises mid-loop, finish
  the scan job `failed` with a static error code before re-raising. Test with a fake
  scanner that raises on the second resource.
- [ ] R4 Missing tests from the Tests section: `TELE-2` is assigned and email
  `times_seen == 2` after the first scan; PATCH `/api/issues/{id}` with an allowed
  transition -> history and audit, a disallowed one -> 409 `invalid_transition`; list
  filters `source`, `rule_class`, `severity`, `assignee`, `q`, `sort=times_seen`, keyset
  cursor paging over 3+ issues, and `/events` paging; stats zero-fill returns exactly
  `days` buckets with zeros for empty days.
- [ ] R5 Accepted deviations to record (no code change): ingest responses are built with
  `json.dumps` over a literal dict passed inline (allowed by conventions section 3);
  `findings_push` records with `scan_job_id=None` and `DetectorKind.POLICE`.
- [ ] R6 Re-run the full Verification block and `wc -l` on every p16 Python file (each
  under 1000); every command exits 0; log paths recorded.

Not in scope: escaping `%`/`_` in the `q` filter (masked columns only, no accepted
requirement).

## Completion Criteria

- [x] IssueService (recorder, reader, transitions), ResourceScannerImpl, IngestService and both routers delivered with the exact names
- [ ] Lifecycle, regression, auto-resolve, skip, resolve_unchecked and leak tests pass (after R1-R4)
- [ ] Run A Resume items R0-R6 checked with evidence
- [ ] Scoped verification passes on the final tree; logs recorded
- [ ] Progress Log updated (run A session)

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 p16 implementation
**Tasks Completed**: Implemented issue grouping, idempotent finding persistence, lifecycle transitions and history, regression and auto-resolution, resource scanning with size skip and post-commit indexing, custom push ingestion, and both REST routers. Added 9 SQLite/API tests covering lifecycle, ignored status, removal reconciliation bounds, scanner skip, direct/external raw-value rejection, persistence/response leak checks, API filters/stats/comments/history pagination, and ingest source/config/findings/content behavior.
**Tasks In Progress**: Formal test-integrity, adversarial, and combined-tree review are owned by later workflow steps.
**Blockers**: None.
**Notes**: Scoped verification passed on the current p16 source. Logs: `/tmp/telescreen-p16-issues-ingest-format-final.log`, `/tmp/telescreen-p16-issues-ingest-ruff-final.log`, `/tmp/telescreen-p16-issues-ingest-ty-final.log`, `/tmp/telescreen-p16-issues-ingest-typelint-final.log`, and `/tmp/telescreen-p16-issues-ingest-pytest-final.log` (9 passed). Earlier implementation attempts exposed timezone-aware SQLite comparisons, a literal stats route shadowed by `{issue_id}`, and test assumptions about UUID ordering/JSON spacing; each was corrected and the final test suite passed. All p16 Python files remain below 1000 lines; the test file is 884 lines.
