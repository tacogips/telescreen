# Collectors, Push Ingestion and Issue Lifecycle

How monitored content enters Telescreen (built-in collectors, custom pushers, police-backed
collectors), how it is scanned transiently, and how findings become Sentry-like issues.

## Overview

A resource (file, message, sheet, repository file) is identified by `(source, raw_locator)`.
Every scan of a resource produces a `ResourceScan` with masked findings and a `complete`
flag. `IssueService.record_scan` groups findings into issues, updates counters, regresses
resolved issues and auto-resolves issues whose rule class disappeared from a complete scan.
Monitored content is never stored.

## Details

### Scan pipeline (`telescreen.issues.scan`)

1. Input: `ResourceContent(source, raw_locator, ref: ResourceRef | None, collector_config_id,
   display_title, segments: tuple[Segment, ...], complete: bool, sweep_covered: bool)`;
   `Segment(locator, text repr=False)`. `sweep_covered` is true when a full sweep of the
   config enumerates this resource. It is false for GitHub `refs/pull/*` and unselected
   refs, and false for `custom` pushes. For `police` configs it is always true, because the
   Police sweep defines that estate. `ref` is stored as `issues.resource_ref_json` under the rule in the data
   model; `ResourceRescanner.rescan(issue_id)` uses it with the owning collector adapter.
2. Segments become `ScanItem`s and are sent to the injected `Detector` in batches.
3. `Masker.seal` produces `MaskedFinding`s (rule_id, rule_class, severity, masked
   snippet, masked location, fingerprint, detector, confidence).
4. `IssueService.record_scan(ResourceScan) -> RecordOutcome` persists, audits, indexes and
   emits `IssueEvent`s (`issue_created`, `issue_regressed`) through the `IssueEventSink`
   port. `RecordOutcome` holds counts only (`findings_inserted`, `issues_opened`,
   `issues_regressed`, `issues_auto_resolved`) plus the affected issue ids, which are used
   by the ingest response and are never stored in sweep summaries.
5. The `ResourceContent` object is released; size limit `TELESCREEN_SCAN_MAX_BYTES`
   (default 5 MiB) per resource; larger resources are skipped and counted in
   `scan_jobs.resources_skipped` with reason code `too_large`. A skipped resource is still
   reported as an incomplete scan with no segments. That report changes no status; it only
   updates `last_checked_at`.
6. Every recorded scan sets `issues.last_checked_at = scan time` on all issues of that
   `resource_key`, whatever their status.
7. In sweeps, a resource whose fetch or export fails is recorded the same way as a skipped
   resource, as an incomplete scan with no segments, and is counted in `resources_failed`.
   Any such failure makes the sweep `partial`, so no removal reconciliation runs. An
   upstream "not found" or trashed state is a removal and is recorded as a complete empty
   scan. See `design-map-reduce-sweeps.md` (Map, Reduce).

`ResourceScanner` (port) exposes `scan_resource(content, scan_job_id)` so collectors, police
results and push ingestion share this path.

### Issue grouping and lifecycle (`telescreen.issues.service`)

- Grouping: one issue per `grouping_key = sha256(source, resource_key, rule_class)` (see
  data model). Multiple distinct values of the same class in the same resource stack into
  one issue; `distinct_fingerprints` counts them.
- Each scan inserts one `findings` row per `(issue, scan_job, fingerprint)`; `times_seen`
  increases by the number of inserted rows; `last_seen` = scan time; `first_seen` set on
  creation.
- If the issue is `resolved` and a finding arrives: status becomes `regressed`,
  `regression_count += 1`, history `regressed`, event `issue_regressed`.
- If the issue is `ignored`: counters update, status unchanged, no event.
- If `complete=True`: every open issue (`unresolved`, `acknowledged`, `in_progress`,
  `regressed`) on the same `resource_key` whose rule class has no finding in this scan is
  set to `resolved` with `resolution=auto` and history `auto_resolved`. A deleted resource
  is reported as a complete scan with no segments.
- Manual transitions follow the status machine in the data model; every transition writes
  `issue_history` and an audit record in the same transaction.
- Stats: `GET /api/issues/{id}/stats?bucket=day|hour&days=14` counts `findings` per bucket
  for the list sparkline.

### Collector adapters

The `CollectorAdapter` Protocol and its DTOs live in `telescreen.ports.collectors` (ports plan, wave 2).
Adapters are implemented in `telescreen.collectors.slack`, `telescreen.collectors.google` (gdrive,
gdocs, gsheets) and `telescreen.collectors.github` by separate plans. The core
(`telescreen.collectors`: service, registry, scheduler provider, rescanner, police kind, task
handlers) depends only on the Protocol.

```python
class CollectorAdapter(Protocol):
    kind: CollectorKind
    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck: ...
    async def resources_from_event(self, event: InboundWebhook, config: CollectorConfig) -> list[ResourceRef]: ...
    async def list_changed(self, config: CollectorConfig, cursor: CollectorCursor | None) -> ChangePage: ...
    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent: ...
    async def renew_watch(self, config: CollectorConfig) -> WatchState | None: ...  # None: no push channel
```

`InboundWebhook` holds the method, the headers (a tuple of name/value pairs), the raw body
bytes and the receipt time. `EventCheck` is `valid`, `invalid` or `challenge(response_body)`.
`ChangePage` holds the changed refs, removed refs, the next cursor and a `full_listing`
flag. When `full_listing` is true the page lists every current resource of the config, and
collectors-core reconciles it as described below. `CollectorRegistry` maps a kind to its adapter. The
router `POST /api/collectors/{kind}/webhook` (slack, gdrive, github; gdocs and gsheets share
the Drive channel) looks the adapter up in the registry. Adapters are registered only in
`telescreen.wiring`.

### Resource identity

A resource's `raw_locator` is stable for the lifetime of the item. It is the only input to
`resource_key`. Segment locators (line, cell, message) and commit shas appear only in
finding `location` and display text, never in `raw_locator`.

| Kind | Resource `raw_locator` | Segment locator (masked, display only) |
|------|------------------------|-----------------------------------------|
| `slack` message | `slack:<team_id>/<channel_id>/<message_ts>` | `message` |
| `slack` file | `slack:<team_id>/file/<file_id>` | `L<n>` |
| `gdrive` | `gdrive:<file_id>` | `L<n>` |
| `gdocs` | `gdocs:<file_id>` | `L<n>` |
| `gsheets` | `gsheets:<file_id>` | `<sheet>!R<r>C<c>` |
| `github` | `github:<host>/<owner>/<repo>@<ref>:<path>` where `<ref>` is the branch name (`refs/heads/<b>`) or `refs/pull/<n>/head`, never a commit sha | `L<n>@<short-sha>` |
| `custom` | `custom:<name>:` + the `locator` given by the pusher | pusher segment locator |
| `police` | `police:<collector-config-name>:` + the finding `locator` given by the police | police `location` |

The display locator is the redacted `raw_locator`. Removal semantics (each produces a
complete scan with no segments for that `raw_locator`, so its open issues auto-resolve):

- Slack: a `message_deleted` or `file_deleted` event, or a message or file missing from a
  schedule listing that collectors-core reconciles.
- Drive kinds: `changes.list` or a push notification reports the file as `removed` or
  `trashed`, or as having left the selected folders or drives.
- GitHub: a push whose commits remove the path from the branch. A forced push
  (`forced: true`), or a compare that fails because history was rewritten, triggers a full
  tree listing of that ref. A complete scan of a GitHub ref always uses a full tree listing.
- Reconciliation for full listings: collectors-core asks
  `IssueReader.open_resource_refs(collector_config_id)` for the refs of open issues. Every ref
  missing from the listing gets a complete empty scan.
- Copies outside the scanned ref (other branches, PR refs, forks, caches, Git LFS) are not
  inferred by collectors. The workflow's `police_verify` (`verify_remediation`) step checks them.

### Per-kind behavior

| Kind | Event trigger | Signature check | Schedule trigger | Content fetched (transient) |
|------|---------------|-----------------|------------------|-----------------------------|
| `slack` | Events API (`message`, `message_changed`, `message_deleted`, `file_shared`, `file_deleted`) | `v0=HMAC-SHA256(signing_secret, "v0:{ts}:{body}")`, 300 s window; `url_verification` is answered as a challenge | `conversations.history` per selected channel since cursor `ts`; `files.list` for text files | message text (one segment) or text file lines |
| `gdrive` | `changes.watch` channel notifications | `X-Goog-Channel-Token` equals `HMAC(key, channel_id)`; the channel id maps to the collector config | `changes.list` with the stored page token | non-Google text files via `files.get?alt=media` |
| `gdocs` | shares the Drive channel (mime filter) | as gdrive | as gdrive, Docs mime type | `files.export` `text/plain` |
| `gsheets` | shares the Drive channel (mime filter) | as gdrive | as gdrive, Sheets mime type | Sheets API `values.batchGet` for all sheets, per cell |
| `github` | webhook (`push`, `pull_request`) | `X-Hub-Signature-256 = sha256=HMAC(secret, body)` | git trees API on the selected repos and branches; incremental by last scanned commit via the compare API (removed files become removals) | text-like blobs under the size limit, per line |
| `custom` | push API | ingest token | none (the pusher decides) | provided by the pusher |
| `police` | none | police contract | cron dispatches `detect` police tasks with the selector as input | none; findings arrive in police results |

- Webhook handlers verify the request, answer quickly (Slack within 3 s) and enqueue
  `collector_scan` engine tasks (idempotency key `collector:<config>:<resource_key>:<event-id>`). They
  never scan inline.
- Drive watch channels are created by `telescreen collector run <name> --watch`. The scheduler
  renews them one day before expiry with a `collector_watch_renew` task that calls
  `renew_watch`.
- Google auth: service-account JWT signed with PyJWT (RS256) and exchanged at the token
  endpoint over httpx; tokens cached in memory until expiry. GitHub: token secret ref; base
  URL `TELESCREEN_GITHUB_API_URL` (Enterprise support). Slack: bot token secret ref.
- Selectors (typed dataclasses stored in `selector_json`): `SlackSelector(channel_ids,
  include_files)`, `DriveSelector(drive_ids, folder_ids, mime_types)`,
  `GitHubSelector(owner, repos, branches, path_globs)`, `CustomSelector(document: JsonValue)`,
  `PoliceSelector(labels, input: JsonValue)`.
- Schedule: `trigger_mode` in (`schedule`, `both`) requires `cron` (5-field, croniter).
  The scheduler enqueues `collector_scan` with idempotency key `collector:<config>:<fire-iso>`.
- Tests: every adapter is tested with `httpx.MockTransport` fakes for event mode (including
  valid, invalid and replayed signatures) and schedule mode (cursor progression).

### Push ingestion API (`/api/ingest/v1`, token scope `ingest`)

| Endpoint | Body | Behavior |
|----------|------|----------|
| `POST /api/ingest/v1/scan` | `{source: "custom:<name>", resources: [{locator, title?, complete, segments: [{locator, text}]}]}` (max 10 MiB) | Content scanned transiently through the scan pipeline; response `{scan_job_id, resources, findings, issue_ids}`; content discarded |
| `POST /api/ingest/v1/findings` | `{source, locator, complete, findings: [{rule_id, rule_class, masked_snippet, location, confidence, external_fingerprint}]}` | Every string field re-scanned by `Redactor`; any hit rejects the request (422 `raw_value_detected`, no echo); fingerprints re-keyed (`ext`) |
| `GET /api/ingest/v1/sources/{name}/config` | - | Returns the `custom` collector config selector so custom crawlers and agents are configured centrally |

`source` for pushers must match an enabled `custom` collector config name; unknown sources are
rejected with 404 `unknown_source`.

### Full sweeps (map-reduce)

Besides event and incremental schedule scans, every built-in kind supports a full sweep of
its whole estate (for example all Drive files of the domain via domain-wide delegation),
started by `sweep_cron`, `telescreen collector sweep <name>`, `POST /api/collectors/{id}/sweep`
or a workflow `map` step. Sweeps are partitioned and scanned concurrently through the
optional `SweepCapable` adapter capability (`partition`, `shard_resources`); collectors of kind
`police` delegate both to Police workers (`partition` / `scan_shard` task types). See
`design-map-reduce-sweeps.md`.

collectors-core owns `src/telescreen/collectors/sweep.py`:

- `CollectorSweeper` port implementation: it resolves the config and adapter through the
  registry, rejects kinds without `SweepCapable` (`sweep_unsupported`), and scans each
  shard resource through `ResourceScanner` with the given `scan_job_id`.
- Sweep starts: the scheduler tick for `sweep_cron` (idempotency key
  `sweep:<config>:<fire-iso>`), the router's `POST /api/collectors/{id}/sweep` and the CLI
  all call the `SweepLauncher` port.
- `SweepLauncher` refuses a second collector-origin sweep of a config while one is not
  terminal (`conflict`, 409 at the API). This is safe for reconciliation even when sweeps
  overlap, because the watermark is per job.

### Assembly bindings (`telescreen.wiring`, run B2)

`build_container` registers every built-in kind in the `CollectorRegistry`, each with its
`SweepCapable` implementation: `slack` (`SlackAdapter`, `SlackSweep`), `gdrive`, `gdocs`
and `gsheets` (`build_google_adapters`, one `GoogleSweep` per kind) and `github`
(`GitHubAdapter`, `GitHubSweep`). `police` and `custom` are not registry adapters.

- Registration is unconditional. A deployment that never configures Slack or Google still
  starts; the adapters exist but fail at use.
- API credentials are resolved lazily inside each adapter's `api_factory(config)`: the
  config's `credential_ref` when set, else the global kind setting
  (`TELESCREEN_SLACK_BOT_TOKEN`, `TELESCREEN_GOOGLE_SERVICE_ACCOUNT`; GitHub has no global
  token). A missing or unresolvable credential raises `ConfigError` inside the scan or
  sweep task. The task fails with the masked error code and never echoes the reference
  target or value.
- Webhook secrets (`WebhookSecrets`) are resolved once at build time with `resolve_secret`:
  slack from `TELESCREEN_SLACK_SIGNING_SECRET`, github from
  `TELESCREEN_GITHUB_WEBHOOK_SECRET`. An unset variable leaves that secret `None`, and
  the adapter then answers every event `invalid` (fail closed, 401). A variable that is set
  but does not resolve raises `ConfigError` at startup (exit 3).
- The Drive channel token key is
  `HMAC-SHA256(fingerprint_key, "telescreen:gdrive-channel-token:v1")`. The same bytes go to
  `build_google_adapters` (watch creation) and to `WebhookSecrets.gdrive`
  (verification), so a channel created by `collector run --watch` always verifies after
  a restart. No new setting is introduced.
- Other inputs come from existing settings: GitHub host from `TELESCREEN_GITHUB_API_URL`;
  size limit `TELESCREEN_SCAN_MAX_BYTES`; Drive shard size `TELESCREEN_SWEEP_SHARD_SIZE`;
  `TELESCREEN_PUBLIC_BASE_URL` for watch callbacks. The Google `AdminApi` for domain-wide
  sweeps is built only when `TELESCREEN_GOOGLE_DELEGATED_ADMIN` is set, else `None`.
- All adapters share the container's single `httpx.AsyncClient`. Tests inject an
  `httpx.MockTransport` through `build_container(http=...)`, so assembly needs no
  per-adapter test seam.

## References

See `design-docs/references/README.md`.
