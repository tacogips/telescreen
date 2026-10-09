# P04 Ports (Cross-Plan Protocols and DTOs) Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/architecture.md section 4 (Ports table), design-docs/specs/design-map-reduce-sweeps.md (Partitioner and shard executor ports), design-docs/specs/design-collectors-and-ingestion.md
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p04-ports`
- planPath: `impl-plans/active/p04-ports.md`
- wave: 2
- dependsOn: `p01-foundation`
- writePaths:
  - `impl-plans/active/p04-ports.md`
  - `src/telescreen/ports`
  - `tests/ports`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Wave-4 feature packages (L3) never import each other. They talk only through the
Protocols and frozen DTOs in `telescreen.ports`, which this plan freezes. Thirteen later
plans code against these exact names and signatures, so implement them EXACTLY as written
here. Do not add, rename or reorder fields. Missing behavior belongs to the implementing
plans.

Layering: `telescreen.ports` imports only `telescreen.core`, stdlib, `datetime` and
`sqlalchemy.ext.asyncio.AsyncSession` (type only).

## Non-goals

- No implementations except tiny pure helper methods named below.
- Workflow executor contracts (`StepContext`, `StepExecutor`, `StepOutcome`) live in
  `telescreen.workflow.step_api` (plan p06), because they need the DSL model. This plan
  holds only the workflow types that non-workflow packages use.

## Conventions for every port

- `session: AsyncSession` as the first parameter means "runs inside the caller's
  transaction; never commits". Methods without `session` open their own transactions.
- All DTOs are `@dataclass(frozen=True)`. Fields that may hold raw content or raw
  locators use `field(repr=False)`.
- Free-form JSON is `JsonValue` (from `telescreen.core.json`).

## Deliverables (module -> exact contents)

### `ports/__init__.py`
Docstring only.

### `ports/audit.py`
- `AuditEntry(actor_kind: ActorKind, actor_ref: str, action: str, target_kind: str, target_id: str, summary: str, details: JsonValue)`.
  `action` uses the form `<area>.<verb>` (for example `issue.status_changed`).
- `class AuditLog(Protocol)`: `async def append(self, session: AsyncSession, entry: AuditEntry) -> int` (returns seq).

### `ports/search.py`
- `class SearchIndexer(Protocol)`: `index_issue(issue_id: str)`, `index_finding(finding_id: str)`,
  `index_run(run_id: str)`, all `async ... -> None`. Called AFTER commit; implementations
  never raise. (Audit records are indexed by a scheduler tick in p15, so there is no
  `index_audit` method.)

### `ports/directory.py`
- `ResolvedRecipients(emails: tuple[str, ...], slack_channels: tuple[str, ...], slack_user_ids: tuple[str, ...], member_ids: tuple[str, ...])`.
- `class RecipientResolver(Protocol)`:
  - `async def resolve(self, session, ref: str) -> ResolvedRecipients` (an unknown ref
    raises `TelescreenError(recipient_unknown)`);
  - `async def is_member_of(self, session, member_id: str, ref: str) -> bool` (true when
    `ref` is `member:<handle>` of that member, or `team:<slug>` containing that member);
  - `async def assignee_of(self, session, ref: str) -> AssigneeIds`, where
    `AssigneeIds(member_id: str | None, team_id: str | None)`. Only `member:` and `team:`
    refs are accepted; others raise `recipient_unknown`. Issues use it to fill
    `assignee_member_id` and `assignee_team_id`.

### `ports/findings.py`
- `MaskedFinding(rule_id: str, rule_class: RuleClass, severity: Severity, detector: DetectorKind, confidence: float, masked_snippet: str, location: str, fingerprint: str, fingerprint_key_id: str)`.
- `ResourceScan(source: str, resource_key: str, display_locator: str, display_title: str, collector_config_id: str | None, scan_job_id: str | None, ref_json: str | None, findings: tuple[MaskedFinding, ...], complete: bool, sweep_covered: bool, observed_at: datetime)`.
  Contains no raw data.
- `ExternalFinding(locator: str, rule_id: str, rule_class: RuleClass, masked_snippet: str, location: str, confidence: float, external_fingerprint: str)`.
- `ExternalScan(source: str, collector_config_id: str | None, scan_job_id: str | None, detector: DetectorKind, findings: tuple[ExternalFinding, ...], complete_locators: tuple[str, ...], sweep_covered: bool, observed_at: datetime)`.
- `RecordOutcome(findings_inserted: int, issues_opened: int, issues_regressed: int, issues_auto_resolved: int, issue_ids: tuple[str, ...], skipped: bool = False)`
  (`skipped` is True when `ResourceScanner` skipped the resource as `too_large`)
  with `@staticmethod empty() -> RecordOutcome` and `plus(other) -> RecordOutcome`.

### `ports/issues.py`
- `IssueHistoryEntry(issue_id: str, kind: IssueHistoryKind, actor_ref: str, at: datetime, note: str | None = None, from_value: str | None = None, to_value: str | None = None)`.
- `class IssueRecorder(Protocol)`:
  - `record_scan(session, scan: ResourceScan) -> RecordOutcome`
  - `record_external(session, scan: ExternalScan) -> RecordOutcome`
  - `add_history(session, entry: IssueHistoryEntry) -> None`
  - `resolve_unchecked(session, collector_config_id: str, checked_before: datetime) -> int`
- `class ResourceScanner(Protocol)`: `async def scan_resource(self, content: ResourceContent, scan_job_id: str, detectors: tuple[DetectorKind, ...] | None = None) -> RecordOutcome`
  (own transaction; detection, masking and recording in one call). `detectors=None`
  means the configured pipeline. An explicit tuple is used by workflow `detect` steps.
- `IssueSnapshot(id: str, number: int, short_id: str, display_title: str, status: IssueStatus, source: str, rule_class: RuleClass, severity: Severity, display_locator: str, times_seen: int, first_seen: datetime, last_seen: datetime, collector_config_id: str | None, ref_json: str | None)`.
- `OpenResourceRef(issue_id: str, resource_key: str, ref_json: str | None, sweep_covered: bool)`.
- `class IssueReader(Protocol)`: `get(session, issue_id: str) -> IssueSnapshot | None`;
  `open_resource_refs(session, collector_config_id: str) -> tuple[OpenResourceRef, ...]`.
- `IssueEvent(kind: IssueEventKind, event_id: str, issue_id: str, source: str, rule_class: RuleClass, severity: Severity, at: datetime)`.
- `class IssueEventSink(Protocol)`: `async def on_issue_event(self, session, event: IssueEvent) -> None`.

### `ports/collectors.py`
- `InboundWebhook(kind: CollectorKind, method: str, received_at: datetime, headers: tuple[tuple[str, str], ...] = field(repr=False), body: bytes = field(repr=False))`
  with the method `header(name: str) -> str | None` (case-insensitive).
- `EventCheckKind` StrEnum: valid, invalid, challenge. `EventCheck(kind: EventCheckKind, response_body: str | None = None, config_hint: str | None = None)`.
- `CollectorSecrets(signing_secret: str | None = field(repr=False), channel_token_key: bytes | None = field(repr=False))`.
- `ResourceRef(kind: CollectorKind, ref_json: str, raw_locator: str = field(repr=False))`.
- `Segment(locator: str, text: str = field(repr=False))`.
- `ResourceContent(source: str, collector_config_id: str | None, segments: tuple[Segment, ...], complete: bool, sweep_covered: bool, raw_locator: str = field(repr=False), display_title: str = field(repr=False), ref: ResourceRef | None = None)`.
- `CollectorCursor(value: str)`.
- `ChangePage(changed: tuple[ResourceRef, ...], removed: tuple[ResourceRef, ...], next_cursor: CollectorCursor | None, full_listing: bool, has_more: bool)`.
- `EventResources(changed: tuple[ResourceRef, ...], removed: tuple[ResourceRef, ...], poll_changes: bool = False)`.
  This refines the design's `list[ResourceRef]` return type so that delete events can
  report removals. `poll_changes=True` asks collectors-core to run an incremental
  `list_changed` scan instead: Drive push notifications carry no file ids, and a GitHub
  forced push needs a full listing.
- Selectors: `SlackSelector(channel_ids: tuple[str, ...], include_files: bool)`,
  `DriveSelector(drive_ids: tuple[str, ...], folder_ids: tuple[str, ...], mime_types: tuple[str, ...])`,
  `GitHubSelector(owner: str, repos: tuple[str, ...], branches: tuple[str, ...], path_globs: tuple[str, ...])`,
  `CustomSelector(document: JsonValue)`, `PoliceSelector(labels: tuple[str, ...], input: JsonValue)`.
  `type CollectorSelector = SlackSelector | DriveSelector | GitHubSelector | CustomSelector | PoliceSelector`.
- `CollectorConfig(id: str, name: str, kind: CollectorKind, trigger_mode: TriggerMode, cron: str | None, sweep_cron: str | None, sweep_max_concurrency: int, selector: CollectorSelector, credential_ref: str | None, enabled: bool)`.
- `WatchState(channel_id: str, resource_id: str, expires_at: datetime)`.
- Exceptions: `class ResourceGoneError(Exception)` (fetch of a deleted or trashed
  resource); `class RateLimited(Exception)` with the field
  `retry_after_seconds: float | None` (declared in the class body, set in `__init__`).
- The adapter Protocol:

```python
class CollectorAdapter(Protocol):
    kind: CollectorKind
    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck: ...
    async def resources_from_event(self, event: InboundWebhook, config: CollectorConfig) -> EventResources: ...
    async def list_changed(self, config: CollectorConfig, cursor: CollectorCursor | None) -> ChangePage: ...
    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent: ...
    async def renew_watch(self, config: CollectorConfig, current: WatchState | None) -> WatchState | None: ...
    def ref_from_json(self, ref_json: str) -> ResourceRef: ...
```

  `ref_from_json` rebuilds a `ResourceRef` (including `raw_locator`) from its opaque
  `ref_json`. Engine task payloads and `issues.resource_ref_json` store only `ref_json`,
  never raw locators. Invalid input raises `TelescreenError(validation_error)`.

- `RescanKind` StrEnum: found, clean, error. `RescanResult(kind: RescanKind, error_code: ErrorCode | None = None)`.
  `class ResourceRescanner(Protocol)`: `async def rescan(self, issue_id: str, detectors: tuple[DetectorKind, ...] | None = None) -> RescanResult`.
- `class CollectorConfigReader(Protocol)`: `get(session, collector_config_id: str) -> CollectorConfig | None`;
  `get_by_name(session, name: str) -> CollectorConfig | None`.
- `class ScanJobStore(Protocol)`:
  - `async def create(self, session, collector_config_id: str | None, source: str, trigger: ScanTrigger, started_at: datetime) -> str`;
  - `async def finish(self, session, scan_job_id: str, status: ScanJobStatus, resources_scanned: int, resources_skipped: int, findings_count: int, error_code: ErrorCode | None, finished_at: datetime) -> None`.

### `ports/sweeps.py` (design-map-reduce-sweeps.md, exactly)
- `PartitionRequest(corpus_key: str, cursor: str | None, selector: CollectorSelector)`.
- `ShardSpec(shard_key: str, descriptor: JsonValue, weight: int)`.
- `SubCorpus(corpus_key: str)`.
- `PartitionPage(shards: tuple[ShardSpec, ...], next_cursor: str | None, sub_corpora: tuple[SubCorpus, ...])`.
- `ShardEntryKind` StrEnum: fetched, removed, failed.
  `ShardEntry(kind: ShardEntryKind, ref: ResourceRef, content: ResourceContent | None = None, error_code: ErrorCode | None = None)`.
- `RuleClassCount(rule_class: RuleClass, count: int)`.
- `ShardSummary(resources_scanned: int, resources_skipped: int, resources_failed: int, bytes_scanned: int, findings_inserted: int, issues_opened: int, issues_auto_resolved: int, findings_by_rule_class: tuple[RuleClassCount, ...])`
  with `empty()` and `plus(other)` helpers (per-class counts summed).
- `ShardFailure(error_code: ErrorCode, retry_after_seconds: float | None = None)`.
- Protocols:
  - `SweepCapable`: `partition(config: CollectorConfig, request: PartitionRequest) -> PartitionPage`;
    `shard_resources(config: CollectorConfig, shard: ShardSpec) -> AsyncIterator[ShardEntry]`
    (a plain `def` returning an async iterator).
  - `CollectorSweeper`: `partition(collector_config_id: str, request: PartitionRequest) -> PartitionPage`;
    `scan_shard(collector_config_id: str, shard: ShardSpec, scan_job_id: str) -> ShardSummary`.
  - `SweepLauncher`: `start_collector_sweep(collector_config_id: str, trigger: ScanTrigger) -> str`.
  - `MapResultSink`: `partition_page(police_task_id: str, page: PartitionPage) -> None`;
    `shard_finished(police_task_id: str, summary: ShardSummary) -> None`;
    `task_failed(police_task_id: str, failure: ShardFailure) -> None`.
    All take `session` first (they run inside police-bureau's result transaction).

### `ports/notifications.py`
- `NotificationRequest(channel: NotificationChannel, recipients: tuple[str, ...], subject: str, body: str, actor_ref: str, run_id: str | None = None, issue_id: str | None = None)`.
- `NotificationReceipt(notification_ids: tuple[str, ...], verdict: DlpVerdict, queued: bool)`.
- `class NotificationSender(Protocol)`: `async def send(self, session, request: NotificationRequest) -> NotificationReceipt`.

### `ports/police.py`
- `PoliceTaskRequest(task_type: str, labels: tuple[str, ...], input: JsonValue, deadline: datetime, step_run_id: str | None = None, collector_config_id: str | None = None, max_attempts: int = 3)`.
- `class PoliceGateway(Protocol)`: `async def dispatch(self, session, request: PoliceTaskRequest) -> str`;
  `async def cancel(self, session, task_id: str) -> None`.

### `ports/evidence.py`
- `class EvidenceQuery(Protocol)`: `async def accepted_count(self, session, issue_id: str, step_run_id: str) -> int`.

### `ports/workflow.py`
- `StepSignalKind` StrEnum: human_completed, evidence_accepted, police_result,
  timer_fired, child_finished, branch_arrived, map_finished.
- `StepSignal(kind: StepSignalKind, outcome: str | None = None, data: JsonValue = field(default_factory=json_null))`.
- `class RunSignaler(Protocol)`: `async def signal(self, session, step_run_id: str, signal: StepSignal) -> None`.
- `TaskSpec(kind: EngineTaskKind, ref_id: str, payload: JsonValue, available_at: datetime, idempotency_key: str, max_attempts: int = 5)`.
- `ClaimedTask(id: str, kind: EngineTaskKind, ref_id: str, payload: JsonValue, attempt: int, max_attempts: int, idempotency_key: str)`.
- `TaskResultKind` StrEnum: done, retry, dead. `TaskResult(kind: TaskResultKind, available_at: datetime | None = None, error_code: ErrorCode | None = None)`
  with static constructors `done()`, `retry(available_at, error_code)`, `dead(error_code)`.
- `class TaskQueue(Protocol)`: `async def enqueue(self, session, spec: TaskSpec) -> bool`
  (False when the idempotency key already exists); `async def cancel_by_ref(self, session, ref_id: str) -> int`.
- `class TaskHandler(Protocol)`: attribute `kind: EngineTaskKind`; `async def handle(self, task: ClaimedTask) -> TaskResult`
  (handlers open their own transactions and must be idempotent).
- `class ScheduleProvider(Protocol)`: `async def tick(self, session, now: datetime) -> int`.

## Key Pitfalls

- Annotate `session` as `AsyncSession` everywhere (import under `TYPE_CHECKING` is NOT
  allowed for runtime dataclasses; import normally).
- Protocol attributes (`kind: CollectorKind`) are plain annotations in the Protocol
  body.
- `type CollectorSelector = ...` is a NON-recursive alias, which is allowed. Do not create
  any recursive alias.
- Do not import `telescreen.detection`, `telescreen.db` or any L3 package.

## Tests (`tests/ports/`)

- Every DTO constructs with the documented fields, is frozen (assigning raises
  `FrozenInstanceError`) and its `repr` omits the `repr=False` fields (use a sample secret
  from `tests.support.samples` as the value and assert absence).
- `RecordOutcome.plus` and `ShardSummary.plus` sum counts, union issue ids, and merge
  per-class counts by class.
- `InboundWebhook.header("x-hub-signature-256")` finds `X-Hub-Signature-256`.
- `TaskResult.retry(...)` sets the kind and fields.
- Minimal fake classes in the test satisfy each Protocol (assign a fake instance to a
  variable annotated with the Protocol; ty checks this).

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/ports tests/ports
uv run --frozen ruff check --no-cache src/telescreen/ports tests/ports
uv run --frozen ty check src/telescreen/ports tests/ports
uv run --frozen pytest -p no:cacheprovider tests/ports 2>&1 | tee /tmp/telescreen-p04-ports-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p04-ports-typelint.log 2>&1; grep -E '^(src/telescreen/ports|tests/ports)' /tmp/telescreen-p04-ports-typelint.log   # prints nothing
grep -rn "telescreen\.\(db\|detection\|masking\|issues\|workflow\|police\|collectors\)" src/telescreen/ports   # prints nothing
```

## Completion Criteria

- [x] Every module and symbol above exists with the exact field names, order and types
- [x] No imports beyond core, stdlib and `AsyncSession`
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 20:05 JST
**Tasks Completed**: Added all assigned `telescreen.ports` frozen DTOs, selectors, enums, pure aggregation helpers, and Protocols; added DTO and structural-Protocol tests; checked every completion criterion.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: None
**Hashes**: All 14 Python source/test hashes are recorded in `tmp/telescreen-v1-20261008-s319/p04-ports/final-source-hashes.sha256`; all source/test paths were absent before this session. Plan before hash `0f1d92432c2592f4edd1adb8496854caf878c4508f627e28bbd8b6b039c59906`; after hash is available in the node-boundary change evidence.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/ports tests/ports` -> exit 0 (14 files already formatted; log `/tmp/telescreen-p04-ports-format.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/ports tests/ports` -> exit 0 (all checks passed; log `/tmp/telescreen-p04-ports-ruff.log`).
- `uv run --frozen ty check src/telescreen/ports tests/ports` -> exit 0 (all checks passed; log `/tmp/telescreen-p04-ports-ty.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/ports 2>&1 | tee /tmp/telescreen-p04-ports-pytest.log` -> exit 0, 5 passed.
- `uv run --frozen python tools/typelint.py > /tmp/telescreen-p04-ports-typelint.log 2>&1; ! grep -E '(/src/telescreen/ports|/tests/ports)' /tmp/telescreen-p04-ports-typelint.log` -> exit 0; no p04 findings.
- `! grep -rn 'telescreen\.\(db\|detection\|masking\|issues\|workflow\|police\|collectors\)' src/telescreen/ports` -> exit 0; no forbidden imports (log `/tmp/telescreen-p04-ports-imports.log`).
**Notes**: Whole-tree typelint reports 5 unrelated violations in `src/telescreen/db/models/__init__.py` and `tests/db/`; p04's filtered gate passes. The earlier full-tree report also captured p04 fake-class TL008 findings, which were corrected and rechecked. Full report: `/tmp/telescreen-p04-ports-typelint-full.log`; current full output: `/tmp/telescreen-p04-ports-typelint.log`.
