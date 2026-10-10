# v1.1: Features Adopted from Comparable Sensitive-Data Monitoring Systems

This document compares Telescreen v1 with the comparable systems surveyed in the owner's
research repository and specifies the features Telescreen adopts in v1.1. The owner asked
on 2026-10-09 to research the similar systems and adopt the features worth adopting.

On 2026-10-10 the specification was checked against the v1 code on `main` (commit 289699e)
and refined so that implementation plans can be written from it. Section 16 lists each
refinement and the reason for it. Table and column details are in `design-data-model.md`
("v1.1 additions"), endpoints are in `design-api-and-ui.md` ("v1.1 endpoints"), the Police
contract minor version is in `design-police-contract.md` ("Contract 1.1"), and the UI is in
`design-ui-sentry.md` section 9.

## Overview

Source research (public documents, source code read at fixed commits, and papers, surveyed
2026-10-09), in `/Users/taco/gits/tacogips/ai-native-research/research/technology/`. These
files are background reading only; implementation does not depend on them.

- `telescreen_related_systems_20261009.md`: a summary of about 50 products, components and
  references.
- `telescreen_related_storage_monitoring_20261009.md`: DSPM tools (Sentra, Rubrik, IBM
  Guardium Exposure Manager, Concentric, Symmetry, Securiti, Cortex, Wiz, Netwrix, Datadog
  SDS).
- `telescreen_related_saas_dlp_20261009.md`: SaaS DLP tools (Teleskope, Nightfall, Strac,
  elba, Metomic, MIND, Polymer, Candor, Harmonic).
- `telescreen_related_enterprise_20261009.md`: enterprise tools (Purview, Google SDP and
  Workspace DLP, Macie, BigID, Cyera, Varonis, Cyberhaven, Log360, P-Pointer, InterSafe PIS,
  Sumizumi-kun).
- `telescreen_related_oss_and_research_20261009.md`: open source tools and research
  (GitGuardian, TruffleHog, Gitleaks, detect-secrets, DefectDojo, OpenMetadata, DataHub;
  IssueGuard, AssetHarvester, RiskHarvester, SecretBench).

The main finding is that the closest products are DSPM tools (data security posture
management). They judge risk as **content x placement x exposure**. Telescreen v1 judges
content only. Other features recur across the survey:

- issue identity and recurrence across copies;
- resolution confirmed by a rescan instead of a manual state change;
- "unscanned is not safe";
- remediation by the data owner, with deadlines;
- minimal evidence.

No surveyed product publicly guarantees all of these together. Telescreen v1 already covers
several of them, and v1.1 closes the remaining gaps.

### Already in v1 (no change needed)

| Capability | Seen in | Telescreen v1 |
|------------|---------|---------------|
| Sentry-style grouping, first/last seen, regression | Sentry, GitGuardian, DefectDojo | Issues with a grouping key, statuses, `regressed` |
| Raw values never persisted; keyed fingerprints | TruffleHog Enterprise (claims), Metomic (hashing) | Masking pipeline, HMAC-SHA256, leak matrix tests |
| In-network scanning that pushes only masked results | Datadog SDS S3, Cyera Outpost, Symmetry, BigID scanners | Police workers (pull/push), SDK, n8n |
| Removal reconciliation only after a complete scan | DefectDojo `close_old_findings` pitfall, IBM | Sweeps reconcile only on `succeeded`; per-resource fetch failures block auto-resolve |
| Full and incremental scans, concurrent sweeps | Sentra (full/sampled, deltas), Macie | Event and schedule triggers, map-reduce sweeps |
| Owner-tier escalation, evidence, verification step | Teleskope wait nodes, Cyera escalation, IBM pending re-scan | Workflow engine: human tasks, SLA tiers, evidence gate, `police_verify`, `detect` |
| DLP on outbound notifications | Workspace DLP snippet pitfall (evidence kept in alerts) | Outbound DLP gate on every notification |
| Pluggable detectors behind one interface | BigID, DataHub custom classifiers | Rule, ML, Claude or Codex, local LLM, cascade |

## Details

### 1. Adoption decisions

| # | Feature | Seen in | v1 gap | Decision |
|---|---------|---------|--------|----------|
| F1 | Exposure context (public link, external share, public channel or repo, Slack Connect) and exposure-aware severity | Sentra, Cyera, Varonis, Rubrik, Strac, elba, Wiz | Content only | **Adopt (v1.1)** |
| F2 | Placement policies: a rule class may be allowed in some locations (for example PII in the HR drive); policies are versioned; a policy change is not a remediation | Rubrik, IBM, Teleskope Policy Maker, DSPM in general | Every detection is a violation | **Adopt (v1.1)** |
| F3 | Resolution reasons and verified resolution (`pending_verification`, decided by a rescan) | IBM (completed -> pending scan -> reopen), Rubrik and Teleskope pitfalls, BigID export-then-resolve pitfall | `manual` / `auto` only | **Adopt (v1.1)** |
| F4 | Per-resource scan coverage: scanned / partial / unsupported (encrypted, password-protected, image, too large) / permission denied / failed; an "unscannable" list | Sentra coverage view, P-Pointer, Macie sampling | Not recorded | **Adopt (v1.1)** |
| F5 | Data-owner routing and self-service: assign to the resource owner (file owner, message author, CODEOWNERS); the owner chooses remove / restrict / business exception; weekly digest; "My tasks" | elba checklist, Teleskope owner DM, Cyera owner routing | The assignee is only a team or member reference | **Adopt (v1.1)** |
| F6 | Collector and Police health alerts: stale collector, expiring watch channel, failing scans, silent Police; sweeps repair lost events | Macie EventBridge best-effort delivery, survey guidance on delivery reliability | Police heartbeat only | **Adopt (v1.1)** |
| F7 | Linking one secret across locations: the same keyed fingerprint in several resources shows as linked occurrences; credential remediation covers all of them; fingerprint key versioning | GitGuardian (one incident per secret), survey HMAC/key-generation guidance | Grouping is per resource | **Adopt (v1.1)** |
| F8 | Risk score: severity x exposure x age x asset type | RiskHarvester (asset value and reachability), Wiz/Varonis prioritization | Severity only | **Adopt (v1.1)** |
| F9 | Exceptions with reason, approver and expiry; automatic reopen on expiry | elba business exceptions, IBM exception approval | `ignored` has no expiry or approver | **Adopt (v1.1)** |
| F10 | Stale-data condition in policies (sensitive data older than N days) | Rubrik (stale), Teleskope (age), elba | None | **Adopt (v1.1)** as an F2 policy condition |
| F11 | Ticket export (Jira, GitHub Issues, ServiceNow) with masked content; an export never resolves an issue | BigID, Cyera, Teleskope, Purview | None | **Adopt later (v1.2)** |
| F12 | Automated remediation behind approval (restrict Drive sharing, revoke public link, hide or delete a Slack message, quarantine) | Strac, Teleskope, Varonis, Metomic, Cyera | Notify and verify only | **Rejected** (owner decision 2026-10-09): Telescreen stays read-only on every source; owners remediate, Telescreen verifies by rescan |
| F13 | Custom document classifiers for Japanese company secrets (labels and examples, LLM or local model) | Concentric, Cyera, MIND custom classifier | Rule classes are value types only | **Adopt later (v1.2)**; evaluated on the synthetic dataset |
| F14 | Credential validity check against the provider | TruffleHog, GitGuardian, detect-secrets | None | **Rejected** (owner decision 2026-10-09): no provider validity checks in any form |
| F15 | On-demand reveal of a matched value, fetched live from the source and never stored | Amazon Macie Reveal | None | **Rejected** (owner decision 2026-10-09): raw values are never revealed; deep links to the source location are allowed |
| F16 | Detector evaluation harness (precision/recall per class; diff in notification volume when a detector changes) | Survey acceptance criteria, IssueGuard/SecretBench methodology | Planned in `design-synthetic-test-dataset.md` | Already planned; scheduled with F13 |
| R1 | Semantic grouping of similar documents, data lineage | Concentric, Cyberhaven | - | **Rejected for now**: needs document embeddings or endpoint telemetry; out of scope |
| R2 | Endpoint and in-transit DLP, AI prompt inspection | Netskope, Forcepoint, Harmonic, Metomic (2026) | - | **Rejected**: a different product category |

v1.1 implements F1 to F10 as specified in sections 2 to 13. F11 to F16, R1 and R2 are out of
scope for this run.

### 2. Shared vocabulary and DTOs

New enums and enum values in `telescreen.core.vocabulary`. Persisted columns are
`String(32)`, so adding values needs no database enum migration.

| Enum | Values (new values in bold where the enum exists) |
|------|------------------------------|
| `Severity` | **`critical`**, `high`, `medium`. `severity_rank(s)` gives critical 3, high 2, medium 1. All ordering uses this function: the trigger `min_severity` filter in `workflow/triggers/service.py` currently calls `(MEDIUM, HIGH).index()` and would raise on `critical`, so it moves to `severity_rank` |
| `IssueStatus` | `unresolved`, `acknowledged`, `in_progress`, `resolved`, `ignored`, `regressed`, **`allowed`**, **`pending_verification`** |
| `IssueResolution` | **`verified_removed`**, **`resource_deleted`**, **`exposure_reduced`**, **`policy_changed`**, **`false_positive`**, **`accepted_risk`**, **`manual_unverified`**. The v1 values `manual` and `auto` stay in the enum so existing rows still parse, but v1.1 never writes them |
| `IssueHistoryKind` | adds `exposure_changed`, `policy_reevaluated`, `verification_requested`, `verification_failed`, `verification_inconclusive`, `exception_requested`, `exception_approved`, `exception_rejected`, `exception_revoked`, `exception_expired` |
| `HumanTaskKind` | adds `owner_review` |
| `EngineTaskKind` | adds `verify_rescan`, `policy_reevaluate`, `owner_digest` |
| `ErrorCode` | adds `scan_incomplete` |
| New | `Visibility` (`public`, `anyone_with_link`, `external_shared`, `domain`, `internal`, `private`, `unknown`), `ChannelKind` (`public`, `private`, `dm`, `connect`), `RepoVisibility` (`public`, `internal`, `private`), `CoverageState` (`scanned`, `partial`, `unsupported`, `too_large`, `permission_denied`, `fetch_failed`), `UnsupportedReason` (`encrypted`, `password_protected`, `image`, `archive_too_deep`, `binary`, `unsupported_type`), `PlacementKind` (`gdrive_drive`, `gdrive_folder`, `slack_channel`, `github_owner`, `github_repo`), `IdentityKind` (`email`, `slack_user`, `github_login`), `IdentityOrigin` (`member_email`, `member_slack`, `manual`), `PolicyEffect` (`allow`, `violate`), `RemediationAction` (`remove`, `restrict`), `ExceptionDuration` (`7d`, `30d`, `90d`, `until_regressed`, `forever`), `ExceptionStatus` (`requested`, `active`, `rejected`, `revoked`, `expired`), `HealthCondition` (`stale_success`, `watch_expiring`, `consecutive_failures`, `unscannable_public`, `police_silent`), `HealthStatus` (`ok`, `warning`, `failing`, `unknown`), `AssetTier` (`cloud_root`, `deploy_token`, `personal_token`, `secret`, `pii_high`, `pii`) |

Status groups, defined once in `vocabulary.py` and used by every repository query:

- `OPEN_STATUSES`: `unresolved`, `acknowledged`, `in_progress`, `regressed`,
  `pending_verification`.
- `RECONCILED_STATUSES`: `OPEN_STATUSES` plus `allowed`. These issues take part in
  complete-scan auto-resolution and in sweep removal reconciliation.
- `CREDENTIAL_CLASSES`: `credential`, `token`, `private_key`, `password`.
- `PII_CLASSES`: `payment_card`, `national_id`, `email`, `phone`, `address`.

DTO additions in `telescreen.ports`. New fields have defaults, so v1 call sites compile
unchanged.

- `ports/collectors.py` (transient, never persisted as-is):
  - `Exposure(visibility, external_principal_count=0, shared_drive=False,
    channel_kind=None, repo_visibility=None, is_fork=False)`.
  - `Coverage(state, reason: UnsupportedReason | None = None, truncated_bytes=0)`.
  - `OwnerIdentity(kind: IdentityKind, value: str = field(repr=False))`.
  - `PlacementRef(kind: PlacementKind, value: str = field(repr=False))`.
  - `ResourceContent` gains `exposure: Exposure | None = None`,
    `owner: OwnerIdentity | None = None`, `placement: tuple[PlacementRef, ...] = ()`,
    `modified_at: datetime | None = None` and `coverage: Coverage | None = None`. `None`
    means "scanned when complete, else partial".
  - `RescanKind` gains `inconclusive`.
- `ports/findings.py`:
  - `MaskedFinding` gains `fingerprint_key_version: int = 1`.
  - `ResourceScan` gains `exposure: Exposure | None`, `owner_ref: str | None`,
    `placement_keys: tuple[str, ...]`, `modified_at: datetime | None` and
    `removed: bool = False`.
  - `ExternalScan` gains `exposures: tuple[LocatorExposure(locator, exposure), ...] = ()`.
- `ports/policy.py` (new). It lets the issues plans work in parallel:
  - `PolicySubject(source, rule_class, placement_keys, visibility, age_reference)` and
    `PolicyDecision(effect, policy_name, policy_version, owner_team, route_to_owner)`.
  - `PolicyDecider.decide(session, subject) -> PolicyDecision`.
  - `RiskInputs(severity, visibility, asset_tier, first_seen, now)` and
    `RiskScorer.score(inputs) -> int`.
- `ports/issues.py`:
  - `IssueSnapshot` gains `assignee_ref`, `risk_score` and `visibility`.
  - New protocol `ResourceIssueReader.open_issues_for_resources(session, resource_keys) ->
    tuple[ResourceIssueRef, ...]` (implemented by issues). It is a separate protocol so
    that existing `IssueReader` fakes and implementations keep conforming.
  - New port `IssueRemediation` (implemented by issues):
    - `request_verification(session, issue_id, action, include_linked, actor_ref, note)`;
    - `request_exception(session, issue_id, reason, duration, actor_ref, is_admin)`;
    - `release_to_team(session, issue_id, actor_ref)`.
- `ports/directory.py`: a new protocol `OwnerDirectory` (implemented by directory). It is
  separate from `RecipientResolver` so that existing resolvers and fakes keep conforming.
  It provides:
  - `member_ref(session, member_id) -> str | None`, which returns `member:<handle>`. This
    fixes the v1 `assignee=me` lookup, which built `member:<id>` while references are keyed
    by handle;
  - `team_refs_of(session, member_id) -> tuple[str, ...]`;
  - `member_for_owner(session, owner_ref) -> str | None`;
  - `team_lead(session, team_slug) -> str | None`.

### 3. F1 Exposure context and exposure-aware severity

Adapters read exposure **transiently** with read-only API calls and return only enums and
counts. Principal emails, domains and names are never kept.

| Source | Provider data (read-only) | Mapping to `visibility` |
|--------|---------------------------|-------------------------|
| Drive (`gdrive`, `gdocs`, `gsheets`) | `permissions.list` (`supportsAllDrives=true`, fields `permissions(type,role,domain,emailAddress,allowFileDiscovery)`); `driveId` | `anyone` with `allowFileDiscovery` -> `public`; other `anyone` -> `anyone_with_link`; any `user`/`group`/`domain` grant outside `TELESCREEN_INTERNAL_DOMAINS` -> `external_shared` (`external_principal_count` = number of such grants); internal `domain` grant -> `domain`; at least one internal user/group grant besides the owner (the `role=owner` grant never counts) -> `internal`; owner only -> `private`. If `TELESCREEN_INTERNAL_DOMAINS` is unset and there are user/group grants, or the permission read fails, the result is `unknown`. `shared_drive` = `driveId` present |
| Slack | `conversations.info` (cached per channel per scan task); file `is_public`, `public_url_shared` | `is_ext_shared` -> `external_shared`, `channel_kind=connect`, count = connected teams other than our own; `is_im` -> `private`, `dm`; `is_mpim` -> `internal`, `dm`; `is_private` -> `internal`, `private`; otherwise `domain`, `public`. A file with `public_url_shared` -> `anyone_with_link` |
| GitHub | Repository JSON (already fetched: `visibility`, `private`, `fork`) | `public` -> `public`; `internal` -> `domain`; `private` -> `private`; `repo_visibility` and `is_fork` copied |
| Police | Optional `resource_exposures` in result contract 1.1 (`design-police-contract.md`) | As sent, per locator; absent -> `unknown` |
| Custom ingest | Not extended | `unknown` |

Widest to narrowest: `public` > `anyone_with_link` > `external_shared` > `domain` >
`internal` > `private`. `unknown` has no rank. In policy conditions it counts as wider than
every limit (fail closed), and it never sets a severity floor.

Persistence (issues): exposure belongs to the resource, so every scan updates all issues
with the same `resource_key`, in the same way it updates `last_checked_at`. The issue keeps
`exposure_json` (canonical JSON of `Exposure`), `exposure_visibility` (for filtering) and
`exposure_changed_at`. A scan that reports `unknown` never overwrites a known value. When
the visibility changes, every issue in `RECONCILED_STATUSES` gets a history entry
`exposure_changed` (from and to visibility values only), and its effective severity, policy
decision (section 4) and risk score (section 10) are recomputed.

Severity: `base_severity` is the rule severity (`severity_for(rule_class)`), and
`severity = max(base_severity, floor)` by `severity_rank`. The floor is set only for
`public` and `anyone_with_link`: `CREDENTIAL_CLASSES` -> `critical` and `PII_CLASSES` ->
`high`. Narrowing the exposure lowers `severity` back to `base_severity`. `IssueEvent`
carries the effective severity.

New settings:

- `TELESCREEN_INTERNAL_DOMAINS`: comma-separated domain list (configuration, not detected
  data).

### 4. F2 and F10 Placement policies

A policy is a YAML or JSON document, versioned like workflow definitions and owned by
`telescreen.issues.policy`:

```yaml
name: hr-records            # ^[a-z0-9][a-z0-9-]{0,63}$
effect: allow               # allow | violate
match:
  rule_classes: [national_id, address, phone, email]   # empty = every class
  sources: [gdrive, gdocs, gsheets]                    # empty = every source
  locations:                                           # omitted or empty = everywhere
    gdrive_drives: ["<shared drive id>"]
    gdrive_folders: ["<parent folder id>"]
    slack_channels: ["<channel id>"]
    github_repos: ["<owner>/<repo>", "<owner>/*"]
condition:                  # allow policies only
  max_exposure: internal    # a Visibility other than unknown
  max_age_days: 365
route_to_owner: true        # default true
owner_team: hr              # existing team slug, optional
rationale: "HR keeps employee records here"   # redacted, max 2000 characters
```

Validation (`POST /api/policies/validate` and on create):

- unknown keys are rejected;
- `condition` on a `violate` policy is rejected;
- `owner_team` must exist;
- `source_text` is scanned with the RuleDetector and any hit rejects the document with 422
  `raw_value_detected`, as for workflow uploads;
- errors carry JSON paths only.

Versioning: creating a policy adds version n+1, inactive. `activate {version}` makes that
version the only active version of the name. `disable` deactivates the name. Create,
activate and disable are admin operations and are audited (`policy.created`,
`policy.activated`, `policy.disabled`) with the actor.

Placement: adapters return raw container identifiers transiently (`PlacementRef`):

- the Drive shared drive id and **immediate** parent folder ids (no ancestry walk);
- the Slack channel id;
- the GitHub owner and `owner/repo`.

`ResourceScannerImpl` stores only keyed references:

```text
placement_key = "<kind>:" + hex(HMAC(identity key, "placement\0" + kind + "\0" + value))
```

Policy selectors are keyed the same way when they are evaluated, so a location matches by
key equality. `<owner>/*` matches the `github_owner` key. Other glob forms are rejected.

Decision (`PolicyDecider.decide`) over enabled, active versions that match rule class,
source and location:

1. Any matching `violate` policy decides: the result is `violate` with the first such policy
   by name.
2. Otherwise, a matching `allow` policy whose conditions all hold decides: the result is
   `allow` with the first such policy by name.
   - `max_exposure`: the visibility is not wider than the limit, and is not `unknown`.
   - `max_age_days` (F10): `now - age_reference <= max_age_days`, where `age_reference` is
     the resource `modified_at` when known (Drive `modifiedTime`, Slack `edited.ts` or `ts`,
     GitHub push commit timestamp), else the issue `first_seen`. Data older than the limit
     violates even in an allowed location.
3. Otherwise the result is `violate`. `owner_team` and `route_to_owner` come from the first
   matching `allow` policy by name whose condition failed. When no policy matched,
   `owner_team` is null and `route_to_owner` = `TELESCREEN_OWNER_ROUTING`.

The issue stores `placement_keys_json`, `resource_modified_at`, `policy_name` and
`policy_version`, so it can be evaluated again without the source.

Re-evaluation: activate and disable enqueue one `policy_reevaluate` engine task (idempotency
key `policy:<name>:<version>:<activated|disabled>:<audit id>`). The task evaluates every
issue in `RECONCILED_STATUSES` and applies only these two changes:

- a violation that now allows -> `allowed`, resolution `policy_changed`, history
  `policy_reevaluated`;
- an `allowed` issue that now violates -> `unresolved`, resolution cleared, history
  `policy_reevaluated`, an `issue_created` event, and owner routing (section 7).

A policy change never writes `verified_removed` or any other remediation reason (avoiding
the Rubrik and Teleskope pitfall where disabling a policy closes violations). `ignored` and
`resolved` issues are not touched. The maintenance tick (section 12) also re-evaluates
`allowed` issues hourly, so a `max_age_days` limit takes effect without a new scan.

### 5. Issue status machine and F3 verified resolution

Record path (`IssueService.record_scan`), per `(resource, rule_class)` with findings, after
the policy decision:

| Current status | Decision `violate` | Decision `allow` |
|----------------|--------------------|------------------|
| none (new issue) | create `unresolved`; `issue_created` event; owner routing | create `allowed`; no event, no notification, no routing |
| `unresolved`, `acknowledged`, `in_progress`, `regressed` | v1 behavior (counters) | `allowed`, resolution `exposure_reduced` (exposure or placement changed) |
| `pending_verification` | `unresolved`, history `verification_failed` (the rescan still finds the value) | `allowed`, resolution `exposure_reduced` |
| `resolved` | `regressed` (v1), `issue_regressed` event | `allowed`, no event |
| `allowed` | `unresolved`, history `policy_reevaluated`, `issue_created` event, owner routing | counters only |
| `ignored` | counters only, except for an `until_regressed` exception (section 11) | counters only |

When a scan moves an open issue to `allowed`, the resolution is `policy_changed` if the
deciding policy `(name, version)` differs from the one stored on the issue (for example, a
policy was activated and its `policy_reevaluate` task has not run yet), and
`exposure_reduced` otherwise.

Auto-resolution:

- When a complete scan (`complete=true`) of a resource has no finding of a class, every
  issue of that resource and class in `RECONCILED_STATUSES` becomes `resolved` with
  resolution `verified_removed`. This replaces v1 `auto`.
- When a scan has `removed=true` (the source reported the resource gone, or an incremental
  change page listed it as removed), the issues of the resource are resolved as
  `resource_deleted`.
- Sweep removal reconciliation (`resolve_unchecked`) also writes `resource_deleted`.
- Incomplete scans never resolve an issue: "unscanned is never clean" (the v1 rule).

User transitions (`PATCH /api/issues/{id}`, field `status`):

| From | To |
|------|----|
| `unresolved` | `acknowledged`, `in_progress` |
| `acknowledged` | `in_progress`, `unresolved` |
| `in_progress` | `unresolved` |
| `regressed` | `acknowledged`, `in_progress` |
| `pending_verification` | `unresolved` (the owner withdraws the remediation claim) |
| `resolved` | `unresolved` (reopen) |
| `ignored` | `unresolved` (revokes an active exception) |
| `allowed` | none (policies decide) |

PATCH to `resolved` or `ignored` returns 422 `invalid_transition`. Those states are reached
only through the operations below and the exception flow (section 11).

- **Mark remediated**: `POST /api/issues/{id}/remediation {action: remove|restrict,
  include_linked, note?}` (scope `write`), allowed from `unresolved`, `acknowledged`,
  `in_progress` and `regressed`:
  - The issue moves to `pending_verification` and gets history `verification_requested`
    (to_value = action, note redacted).
  - When the issue has `resource_ref_json` and `collector_config_id`, a `verify_rescan`
    engine task is enqueued (key `verify:<issue_id>:<history_id>`). Otherwise the next scan
    that covers the resource decides.
  - With `include_linked=true` (credential classes only), the same is applied to every
    linked open occurrence (section 9). The owner confirms the rotation once, and each
    location still needs its own `verified_removed`.
- **`verify_rescan` task** (issues package, using the `ResourceRescanner` port): the rescan
  records through `record_scan`, which decides the result:
  - no finding in a complete scan -> `verified_removed`;
  - a finding -> reopen;
  - an allow decision -> `allowed` with `exposure_reduced`, which is how `restrict`
    resolves.

  `RescanKind.inconclusive` (incomplete content, too large or unsupported) and `error`
  leave the issue `pending_verification`, add history `verification_inconclusive` (to_value
  = coverage state or error code) and notify the assignee reference when one exists. The
  issue is never resolved by this path.
- **`detect` node**: `CollectorRescanner` returns `clean` only for complete content and
  `inconclusive` for incomplete content. The node maps `inconclusive` to outcome `error`
  with `error_code=scan_incomplete`, so the DSL `Outcomes3` (`found`, `clean`, `error`) is
  unchanged. Because the rescan records through `record_scan`, the issue status follows the
  same rules as above.
- **Resolve with a reason**: `POST /api/issues/{id}/resolve {reason, note}`, where `note` is
  required (redacted, at most 4000 characters):
  - `false_positive`: scope `write`.
  - `manual_unverified`: scope `admin`, shown with a warning icon.

  Both are allowed from `OPEN_STATUSES`, resolve immediately and write history
  `status_changed` with the note.

### 6. F4 Scan coverage

- **Producing coverage.** Adapters set `ResourceContent.coverage` where they already branch:
  - oversized or unsupported MIME -> `unsupported` with a reason (`image`, `binary`,
    `unsupported_type`; `encrypted`, `password_protected` and `archive_too_deep` when the
    adapter can tell);
  - truncated content -> `partial` with `truncated_bytes`;
  - provider 403 or a non-rate-limit 404 on a known resource -> `permission_denied`
    (today these map to `fetch_failed`);
  - other errors -> `fetch_failed`.

  The scanner sets `too_large` when content exceeds `TELESCREEN_SCAN_MAX_BYTES`. A resource
  whose coverage state is not `scanned` is always recorded with `complete=false`.
- **Recording** (collectors package, in the scan job transaction):
  - `scan_coverage` keeps a count per `(scan_job_id, state, reason)`.
  - `resource_coverage` keeps one row per `(collector_config_id, resource_key)` for each
    resource whose latest state is not `scanned`, with `state`, `reason`,
    `truncated_bytes`, `exposure_visibility`, `first_seen_at` and `last_attempt_at`. A
    later `scanned` result or a removal deletes the row.
  - Police `scan_shard` failures stay visible through the map-job counts
    (`resources_failed`, failed shard keys). Police shards have no scan job and the contract
    has no per-resource reason, so no `scan_coverage` or `resource_coverage` rows are
    written for them.
- **What a coverage row holds.** Keys, enums, counts and timestamps only. There is no
  locator, title, file name or email. In the API and UI, an unscannable resource is shown by
  its keyed id prefix (12 hex characters), source, state, reason and exposure, plus the
  open issues of the same `resource_key` (through
  `IssueReader.open_issues_for_resources`). An issue carries the v1 masked display locator.
- **Unscannable resources are never clean.** They never auto-resolve an issue, which is the
  v1 rule, now enforced in the rescanner as well (section 5).
- **Public unscannable resources.** A `resource_coverage` row with visibility `public` or
  `anyone_with_link` raises the collector health condition `unscannable_public`
  (section 8). Turn this off with `TELESCREEN_ALERT_UNSCANNABLE_PUBLIC=false`. It does not
  create an issue (section 16, R-6).

### 7. F5 Owner routing, owner review, digest and My tasks

**Owner identity.** Adapters return `OwnerIdentity` transiently:

- Drive: `owners[0].emailAddress` (kind `email`); shared-drive items have none.
- Slack: message or file `user` (kind `slack_user`).
- GitHub:
  - The first matching `CODEOWNERS` owner: `@login` -> `github_login`, an email ->
    `email`. Team entries are ignored. `CODEOWNERS` is fetched once per repository and ref
    per scan task.
  - Otherwise, for push events only, the head commit author `username` (`github_login`).

The scanner stores only:

```text
owner_ref = "<kind>:" + hex(HMAC(identity key, "identity\0" + kind + "\0" + normalized))
```

Normalization: email NFKC lowercased; login lowercased; Slack id as is. Issue rows never
hold a raw email, login or user id.

**Directory mapping.** The `member_identities` table, owned by `telescreen.directory`, holds
`member_id`, `kind`, `identity_key`, `origin` and `created_at`, with
`UNIQUE(kind, identity_key)`.

- On member create or update, the directory derives rows from `members.email` (`email`,
  origin `member_email`) and `members.slack_user_id` (`slack_user`, origin `member_slack`).
- Admins add GitHub logins or extra emails with
  `POST /api/members/{handle}/identities {kind, value}`. Only the key is stored. Responses
  show `kind`, `origin`, an 8-hex key prefix and `created_at`.
- `RecipientResolver.member_for_owner(owner_ref)` returns `member:<handle>` for an active
  mapped member.

**Assignment.** Routing runs when an issue is created as a violation or leaves `allowed`,
and only when it has no assignee:

1. When `decision.route_to_owner` is true and the owner maps to an active member, assign the
   member.
2. Otherwise, assign `decision.owner_team`.
3. Otherwise, assign `TELESCREEN_DEFAULT_TEAM`.
4. Otherwise, leave it unassigned.

Routing writes history `assigned` with actor `system:owner-routing`. Manual assignments are
never overridden.

**Team lead.** `teams.lead_member_id` (nullable), set through the teams API (`lead` handle).
It is the approver for exceptions (section 11). There is no new authorization role: admins
can always act.

**`owner_review` human task.** It is a workflow `human_task` step with `kind: owner_review`.

- The step must declare exactly the outcomes `remove`, `restrict`, `exception` and
  `not_mine`.
- `assignee` may be the special reference `issue:assignee`, resolved when the task is
  created from `IssueSnapshot.assignee_ref`. An unassigned issue fails the step with
  `recipient_unknown`.
- On completion, the human-tasks router calls `IssueRemediation` in the completion
  transaction before signalling the run:
  - `remove` / `restrict` -> `request_verification` (section 5);
  - `exception` -> `request_exception`, with the reason taken from the required `comment`
    and the duration from the new body field `exception_duration` (`7d`, `30d`, `90d`,
    `until_regressed`);
  - `not_mine` -> `release_to_team`, which clears the member assignee and assigns
    `decision.owner_team`, else the default team.
- Delivery: a member assignee gets a Slack DM when the member has `slack_user_id`, else an
  email. A team assignee is notified as in v1.
- Message content: short id, rule class, effective severity, visibility, risk, and the
  `/my-tasks` link built from `TELESCREEN_PUBLIC_BASE_URL`. There is no title or locator,
  because display titles can be file names.
- `examples/workflows` gains an owner-review example.

**Weekly digest.** `TELESCREEN_DIGEST_CRON` (default `0 9 * * 1`, UTC; an empty value
disables it). The issues maintenance provider enqueues one `owner_digest` task per cron
fire (key `digest:<fire minute>`). For each active member with at least one item, the task
sends one notification to `member:<handle>` (Slack DM, else email) through
`NotificationSender` and therefore the DLP gate. The notification contains:

- counts of open issues assigned to the member, by status;
- up to 20 lines in the form `TELE-<n> <rule_class> <severity> <visibility> risk <score>`,
  ordered by risk;
- the short ids of exception requests waiting for the member's approval;
- the short ids of active exceptions on the member's issues that expire within 7 days.

The digest contains no titles, locators or emails.

**My tasks.** The UI page uses existing and extended list endpoints. There is no aggregate
endpoint, which keeps the L3 packages independent:

- `GET /api/human-tasks?assignee=me&status=open` (fixed to resolve the member handle and
  include the member's teams);
- `GET /api/exceptions?approver=me&status=requested`;
- `GET /api/issues?assignee=me` with open statuses, sorted by risk.

New settings:

- `TELESCREEN_OWNER_ROUTING` (bool, default `false`);
- `TELESCREEN_DEFAULT_TEAM` (team slug, optional);
- `TELESCREEN_DIGEST_CRON`.

### 8. F6 Collector and Police health

**Collector health.** The `collector_health` table, owned by collectors, has one row per
config:

- `last_success_at`, `last_failure_at`, `consecutive_failures`;
- `last_event_at`, `watch_expires_at`;
- `open_conditions` (comma list of `HealthCondition`), `alerted_at`, `updated_at`.

Collectors update it when a scan job finishes:

- `succeeded` sets `last_success_at` and resets the failures;
- `failed` increments `consecutive_failures`;
- `partial` changes neither.

Sweep reduce results, accepted webhooks (`last_event_at`) and watch start or renew
(`watch_expires_at`) also update it.

**Collector conditions.** `CollectorHealthProvider` (a `ScheduleProvider`) evaluates them at
most once per minute per process. It is idempotent.

| Condition | Rule |
|-----------|------|
| `stale_success` | The config is enabled and has `cron`, and no success in 2 expected intervals. The interval is the difference between two consecutive croniter fires. Configs younger than 2 intervals are skipped. Sweep-only configs (`sweep_cron` without `cron`) are not evaluated: sweep results finish in the map-reduce reducer, and their failures are already visible as map-job status |
| `watch_expiring` | `watch_expires_at` is within 12 hours. The scheduler starts renewal 24 hours ahead, so the condition means renewal has been failing for about 12 hours (section 16, R-8) |
| `consecutive_failures` | `consecutive_failures >= TELESCREEN_HEALTH_FAILURE_THRESHOLD` (default 3) |
| `unscannable_public` | The config has `resource_coverage` rows with visibility `public` or `anyone_with_link` (section 6) |

**Police health.**

- `police_workers` gains `health_alerted_at`.
- An empty long-poll claim now also updates `last_heartbeat_at`, because a claim shows the
  worker is alive.
- `PoliceHealthProvider` raises `police_silent` for an active **pull** worker when its
  `last_heartbeat_at` is older than 5 minutes (or null) and a matching task (task type and
  labels) has been `queued` for more than 5 minutes.
- Push workers have no heartbeat. Their health shows `last_delivery_at` only.

**Alerts.**

- A notification is sent only when a condition opens. It goes to the recipient references
  in `TELESCREEN_HEALTH_RECIPIENTS` through `NotificationSender`. When the setting is unset,
  the state is kept and shown but no alert is sent.
- The content is the collector or worker name (configured by an operator), the kind, the
  condition and timestamps or counts.
- A condition clears silently. If it opens again, a new alert is sent.

**API and UI.** `GET /api/collectors` and `GET /api/police/workers` items gain
`health {status, conditions, ...}`. `status` is `failing` when any condition is open,
`warning` for an event-only config with no schedule, `unknown` before the first job, and
`ok` otherwise. The Collectors page also flags event-only configs that have no schedule,
since those cannot repair lost events. A separate health endpoint is not added (section 16,
R-7).

New settings:

- `TELESCREEN_HEALTH_RECIPIENTS`;
- `TELESCREEN_HEALTH_FAILURE_THRESHOLD`;
- `TELESCREEN_ALERT_UNSCANNABLE_PUBLIC` (default `true`).

### 9. F7 Linked occurrences and fingerprint key versioning

**Key ring** (`telescreen.masking`):

- `TELESCREEN_FINGERPRINT_KEYS` is a comma-separated list of `<version>=<secret ref>`, for
  example `1=env:TS_FP_V1,2=file:/run/secrets/fp_v2`.
- `TELESCREEN_FINGERPRINT_ACTIVE_VERSION` defaults to the highest version.
- `TELESCREEN_FINGERPRINT_KEY` stays valid and means `1=<ref>`. Setting both variables is a
  configuration error (exit 3). Version 1 is required.
- `Fingerprinter` keeps its type and call sites. `wiring_builders.py` builds it from the
  key list, and it gains `key_version` and a separate identity key:
  - The identity key is version 1. `resource_key`, the new `placement_key` and
    `identity_key` methods, and the Drive channel token use it, so a rotation never changes
    issue identity or splits issues. Version 1 therefore cannot be retired in v1.1.
  - The active key derives `fingerprint` and `external` (the re-keying of Police and ingest
    fingerprints). `key_id` and `key_version` describe the active key.
- Consumers keep receiving one `Fingerprinter`, so no constructor in `wiring.py` changes
  for key versioning. The fingerprint message format (`v1\0` ...) is unchanged.

**Findings.**

- `findings.fingerprint_key_version` (integer, migration backfill 1) is stored next to the
  existing `fingerprint_key_id`.
- `distinct_fingerprints` counts distinct fingerprints of the highest key version present
  on the issue, so a rotation does not double the count.
- A fingerprint is new for `until_regressed` purposes (section 11) only when the issue
  already has findings of the same version.

**Linked occurrences.** A repository query, with no table or view (section 16, R-9):

- For an issue of a `CREDENTIAL_CLASSES` class, it returns the other issues that are not
  `resolved` and have a finding with an equal `(fingerprint_key_version, fingerprint)`.
- Limit 100.
- Index `ix_findings_key_fingerprint (fingerprint_key_version, fingerprint)`.
- External fingerprints are re-keyed per source, so links between Police sources and
  built-in collectors are not possible. This is documented in the UI.
- `GET /api/issues/{id}/occurrences` returns linked issue ids, short ids, source, the v1
  masked display locator, status, severity and visibility, plus the key versions involved.
  The UI explains that a key rotation starts new linking groups.

### 10. F8 Risk score

Formula and stored fields:

```text
risk_score = round(100 * S * E * A * G)
```

- The result is clamped to 0-100 and stored as `issues.risk_score` (integer). It is an
  ordinal score for sorting, not a probability.
- `asset_tier` is stored next to it, as the highest tier among the issue's findings so far.

Default weights (overridable with `TELESCREEN_RISK_WEIGHTS`, a comma list of
`<group>.<name>=<value>` with 0 < value <= 1; unknown keys are a configuration error):

| Factor | Defaults |
|--------|----------|
| S (effective severity) | critical 1.0, high 0.75, medium 0.45 |
| E (visibility) | public 1.0, anyone_with_link 0.9, external_shared 0.8, domain 0.6, unknown 0.6, internal 0.45, private 0.3 |
| A (asset tier) | cloud_root 1.0, deploy_token 0.85, personal_token 0.75, secret 0.65, pii_high 0.6, pii 0.4 |
| G (age since `first_seen`) | less than 1 day 0.7, 1-7 days 0.8, 7-30 days 0.9, 30 days or more 1.0 |

Asset tier by rule id:

| Rule ids | Tier |
|----------|------|
| `aws_access_key_id`, `aws_secret_access_key`, `private_key` | `cloud_root` |
| `slack_token`, `google_api_key` | `deploy_token` |
| `github_token`, `jwt` | `personal_token` |
| `password_assignment` | `secret` |
| `credit_card`, `my_number` | `pii_high` |
| other PII rules | `pii` |

Unknown rule ids (Police, ingest, LLM) fall back by class: `private_key` -> `cloud_root`,
`credential` and `token` -> `personal_token`, `password` -> `secret`, `payment_card` and
`national_id` -> `pii_high`, other PII -> `pii`. For example, a public AWS key seen 10 days
ago scores 1.0 x 1.0 x 1.0 x 0.9 = 90. No provider validity signal is used (owner decision).

When the score is computed:

- at record time;
- on an exposure or severity change;
- on a policy decision change;
- by the hourly maintenance refresh of `OPEN_STATUSES` issues (age). Changed issues are
  re-indexed.

Other statuses keep their last value. Right after the migration the score is 0 until the
first maintenance tick, which runs on the first worker poll.

Where the score is used:

- `GET /api/issues?sort=risk` (keyset `risk_score desc, id`);
- the search issue document (sortable `risk_score`, filterable `exposure`);
- escalation tiers. A tier gains an optional `min_risk` (0-100). When SLA escalation
  reaches a tier whose `min_risk` is above the issue's current `risk_score`, the escalation
  stops there: no reassignment and no notification, and history `sla_breached` is written
  with the note "below risk threshold". Tiers without `min_risk` behave as in v1.

### 11. F9 Exceptions with approval and expiry

The `issue_exceptions` table, owned by issues, has these columns: `issue_id`, `reason`
(redacted), `duration`, `status`, `requested_by`, `approver_ref`, `decided_by`,
`decided_at`, `expires_at`, `created_at`. An issue has at most one `requested` or `active`
exception (409 `conflict`).

- **Request.** `POST /api/issues/{id}/exceptions {reason, duration}` (scope `write`) from
  `OPEN_STATUSES`. `forever` requires `admin`.
  - `approver_ref` is the team lead of the deciding team: the policy `owner_team`, else the
    assignee team, else `TELESCREEN_DEFAULT_TEAM`. When there is no lead, only admins can
    approve.
  - When the requester is an admin, the exception is active at once and the requester is
    recorded as approver.
  - Otherwise the exception is `requested`, the issue status is unchanged, history
    `exception_requested` is written and the approver is notified (short id only).
- **Approve** (`POST /api/exceptions/{id}/approve`) or **reject** (`.../reject`). Only the
  approver member or an admin may act, and a non-admin requester cannot approve their own
  request.
  - Approve makes the exception `active`. `expires_at` = decision time + duration, or null
    for `until_regressed` and `forever`. The issue becomes `ignored` with resolution
    `accepted_risk`, history `exception_approved`.
  - Reject sets `rejected`, history `exception_rejected`.
- **Revoke** (`POST /api/exceptions/{id}/revoke`) by the requester, the approver or an
  admin. PATCH `ignored -> unresolved` also revokes. The issue becomes `unresolved`, history
  `exception_revoked`.
- **Expiry.**
  - Date-based: the maintenance tick sets `expired` when `expires_at <= now`. The issue
    becomes `unresolved` with the resolution cleared, history `exception_expired`, and the
    assignee is notified.
  - `until_regressed`: a new fingerprint (section 9) on the ignored issue expires the
    exception, and the issue becomes `regressed` with an `issue_regressed` event.
- **Closing a request.** When an issue with a `requested` exception leaves `OPEN_STATUSES`
  (resolved or allowed), the request becomes `rejected` with `decided_by=system`. Approving
  a request whose issue is no longer open returns 409 `conflict`.
- **Listing.** `GET /api/exceptions?status&issue_id&approver=me`.
- **Legacy.** v1 `ignored` issues without an exception row stay ignored, and the UI labels
  them "No expiry (legacy)".

### 12. Scheduled maintenance

These new `ScheduleProvider`s are added to the tuple in `wiring.py` and run in the existing
worker scheduler tick. Each is idempotent and guarded to run at most once per interval per
process.

| Provider | Package | Interval | Work |
|----------|---------|----------|------|
| `IssueMaintenanceProvider` | issues | 60 s | Exception expiry; enqueue `owner_digest` on cron fire |
| (same, hourly branch) | issues | 1 h | Risk refresh of open issues; re-evaluate `max_age_days` for `allowed` issues |
| `CollectorHealthProvider` | collectors | 60 s | Collector health conditions and alerts |
| `PoliceHealthProvider` | police | 60 s | `police_silent` |

The task handlers `verify_rescan`, `policy_reevaluate` and `owner_digest` are registered by
issues through the `TaskHandler` port.

### 13. Data handling and the leak matrix

- New tables and columns hold only enums, counts, timestamps, keyed references (HMAC hex),
  ids, operator configuration (policy selectors, collector and worker names) and redacted
  text (policy rationale, exception reason, notes). They never hold raw values, file names,
  paths, titles or emails:
  - exposure, placement and owner columns on `issues`;
  - `placement_policies`, `resource_coverage`, `scan_coverage`, `collector_health`,
    `member_identities` and `issue_exceptions`.

  v1 fields (`display_title`, `display_locator`) keep their v1 masked semantics.
- Provider principals (emails, domains, user ids) are read only inside the adapter
  coroutine and reduced to enums, counts or keyed references before leaving it. Like
  `raw_locator`, they are `repr=False` fields.
- Digest, owner-review, exception and health notifications contain no titles, locators or
  emails, and they pass the DLP gate.
- The leak matrix (`tests/leak`) gains:
  - flows for policy create and re-evaluation, remediation and verification rescan,
    exception request, approval and expiry, owner routing with a manual identity, a
    coverage row (unsupported and public), a health alert, the digest, and Police 1.1
    `resource_exposures`;
  - a canary file name and a canary owner email (example domain) in the harness source
    content and metadata. The canaries must not appear in the new tables and columns, the
    new notifications or the new API responses. Raw samples must not appear in any sink.

  The generic all-table database sink covers new tables automatically. Mock UI fixtures stay
  under the fixture value test.

### 14. Implementation plan shape (v1.1 run)

The plans follow `impl-plans/conventions.md`, including section 7a. Each plan file stays
under 400 lines and each Python file under 1000 lines. Known split points:

- `workflow/dsl/loader.py` (956 lines) must be split before it gains `owner_review` and
  `min_risk` parsing;
- `issues/service.py` (547 lines) is split into record, lifecycle and exception modules;
- new tests go into new files instead of `tests/issues/test_issues.py` (906 lines) and
  `tests/workflow/engine/test_resume_gaps.py` (895 lines).

Write paths are disjoint. Wave DAG:

| Wave | Plan (suggested id) | Owns |
|------|---------------------|------|
| 1 | v11-foundation | `core/vocabulary.py`, `core/config.py` (all new settings of this document), `ports/**`, `masking/**` (versioned `Fingerprinter`, Masker key version), `wiring_builders.py`, `workflow/triggers/service.py` (`severity_rank` fix only), `tests/core/**`, `tests/masking/**`, `tests/ports/**` |
| 1 | v11-db-migration | `db/models/**`, `db/migrations/versions/0002_v1_1.py`, `tests/db/**` |
| 1 | v11-police-contract | `contracts/police/v1/**` (1.1 schemas and examples), `telescreen_police_sdk/**`, `tests/contract/**`, `tests/police_sdk/**` |
| 1 | v11-ui-foundation | `src/web/src/api/**` (types, client, mock router and fixtures), `src/web/src/components/**`, `src/web/src/styles/**`, `src/web/src/{routes,App}.tsx`, `src/web/scripts/**` |
| 2 | v11-issues-record | `issues/{service,recording,exposure,routing,policy_apply,scan,ingest,transitions,repository}.py` (record path, status machine, PATCH transitions, owner routing, policy re-evaluation application, risk refresh, `until_regressed` expiry on record), `tests/issues/test_issues.py`, `tests/issues/test_issues_api.py`, `tests/issues/v11_record/**` |
| 2 | v11-issues-remediation | `issues/{lifecycle,exceptions,occurrences,verification,digest,maintenance}.py` (remediation, resolve with reason, release to team, exceptions, occurrences, `verify_rescan`, digest, exception expiry), `tests/issues/v11_remediation/**` |
| 2 | v11-policy-risk | `issues/policy/**`, `issues/risk.py`, `tests/issues/v11_policy/**` |
| 2 | v11-collectors-core | `collectors/{tasks,sweep,service,rescanner,scheduler,repository}.py`, new `collectors/coverage.py` and `collectors/health.py`, `tests/collectors/core/**` |
| 2 | v11-collectors-slack / -google / -github | Their adapter directories and tests |
| 2 | v11-police-bureau | `police/**`, `tests/police/**` |
| 2 | v11-workflow | `workflow/dsl/**` (loader split), `workflow/executors/{human,detect}.py`, `workflow/escalation/**`, `tests/workflow/**` (new files) |
| 2 | v11-directory-search | `directory/**`, `search/**` and their tests |
| 2 | v11-ui-issues | `src/web/src/pages/issues/**` (depends only on v11-ui-foundation) |
| 2 | v11-ui-ops | `src/web/src/pages/{tasks,policies,collectors,police,escalation,settings}/**` (depends only on v11-ui-foundation) |
| 3 | v11-api-issues | `api/routers/{issues,exceptions,policies}.py`, `tests/api/v11_issues/**` |
| 3 | v11-api-ops | `api/routers/{collectors,police,directory,human_tasks}.py` (health, coverage, identities, team lead, `assignee=me` fix, owner_review completion), `tests/api/v11_ops/**` |
| 4 | v11-wiring | `wiring.py`, `wiring_collectors.py`, `tests/e2e/test_wiring_cli.py`, `tests/e2e/test_wiring_collectors.py`, `tests/e2e/test_api_routes.py` |
| 4 | v11-docs-examples | `README.md`, `examples/workflows/**`, `examples/compose/**`, `tests/examples/**` |
| 5 | v11-leak-e2e | `tests/leak/**`, new `tests/e2e/test_flow_v11_*.py` |

`wiring.py` builds every router, so router work (wave 3) and binding (wave 4) are
separate waves. This keeps the two router plans parallel without coordinating inside one
file.

Wave-2 and wave-3 plans must keep `src/telescreen/wiring.py` (owned by v11-wiring in
wave 4) type-checking and its tests passing. New constructor dependencies of existing
services and routers are added as dataclass fields with safe defaults that turn the
feature off: `None` ports, no policies, owner routing off. v11-wiring binds the real
implementations. The
foundation's DTO additions are all defaulted for the same reason (section 2).

No dependency changes are needed: `croniter`, `httpx` and `jsonschema` are already present.
If a plan finds one is needed, it goes to v11-foundation by serial reconciliation.

### 15. Owner decisions (resolved 2026-10-09)

- Credential validity checks: rejected (`design-docs/user-qa/qa-credential-validity-check.md`).
- On-demand reveal of raw values: rejected; deep links to the source location only
  (`design-docs/user-qa/qa-on-demand-reveal.md`).
- Write scopes and automated remediation: rejected; Telescreen is read-only on every source
  (`design-docs/user-qa/qa-automated-remediation-scopes.md`). The F5 owner outcomes `remove`
  and `restrict` are actions the owner performs in the source, and Telescreen only verifies
  them by rescan. All new provider calls (`permissions.list`, `conversations.info`,
  `CODEOWNERS` contents) use read-only scopes.

No new owner decision is required. The open points from intake are design decisions
recorded in section 16.

### 16. Refinements against the v1 code (2026-10-10)

| Id | Refinement | Reason |
|----|------------|--------|
| R-1 | Key versioning keeps version 1 as the identity key for resource, grouping, placement and owner keys; only value fingerprints use the active version. Existing findings are backfilled with version 1 | `Fingerprinter.resource_key` uses the same key as value fingerprints, so rotating it would re-key every `resource_key` and `grouping_key`. Old issues would then be duplicated and sweep-resolved, which would count a key change as remediation |
| R-2 | The issue keeps `severity` as the **effective** severity and adds `base_severity` | Existing filters, search documents, triggers and the UI read `severity` |
| R-3 | PATCH can no longer set `resolved` or `ignored`; dedicated remediation, resolve and exception operations replace it. `manual` and `auto` remain readable legacy values | v1 lets any `write` token resolve or ignore without a reason, which conflicts with F3 and F9 |
| R-4 | Placement is stored as keyed references to immediate containers. Repository selectors are exact or `owner/*`, with no path globs. Age falls back to `first_seen` | Policies must be re-evaluated without the source, and new records may not hold paths or file names |
| R-5 | Exception approval is an exception record decided through `/api/exceptions`, not a workflow human task. The approver is the new `teams.lead_member_id` or an admin | `human_tasks` rows require a workflow run and step run, while exceptions apply to any issue. The intake noted that the authorization model had no lead role |
| R-6 | Public unscannable resources raise the collector health condition `unscannable_public` instead of creating an "informational issue" | An issue needs a detected rule class. A pseudo rule class would leak into detector label sets, rule filters, triggers and grouping, and there is no informational severity |
| R-7 | Health is reported on the existing collector and police list endpoints; no separate health endpoint, and no `health_alerts` table | The UI has no dashboard page, and per-package state keeps L3 packages independent |
| R-8 | `watch_expiring` uses a 12-hour window. The failure threshold N defaults to 3 (`TELESCREEN_HEALTH_FAILURE_THRESHOLD`) | The v1 scheduler already renews 24 hours ahead, so a 24-hour window would fire before every renewal. Intake asked for a documented N |
| R-9 | `secret_occurrences` is a repository query over `findings` with a new index, not a table or view | The data already exists in `findings`, so a copy would need to be kept in sync |
| R-10 | The Police contract adds `resource_exposures` (per locator) rather than a single `exposure` object | One result can cover many locators |
| R-11 | My tasks uses three list endpoints (with the `assignee=me` fix), not an aggregate endpoint | It keeps the router-per-package layering; the v1 `assignee=me` built `member:<id>` while references are keyed by handle |
| R-12 | The rescanner reports `inconclusive` for incomplete content, and the `detect` node maps it to `error`/`scan_incomplete` | v1 `CollectorRescanner` returned `clean` whenever the issue was not in the outcome, even for incomplete content |

## References

Primary sources are cited in the research files listed in the Overview. Key ones:

- IBM issue states: https://www.ibm.com/docs/en/exposure-manager?topic=managing-issues
- Rubrik policies: https://docs.rubrik.com/en-us/saas/dspm/dsp_policies.html
- Teleskope Policy Maker: https://docs.teleskope.ai/the-platform/policy-maker
- Nightfall deduplication: https://help.nightfall.ai/dashboard/sdp_events/deduplication
- Sentra coverage: https://sentra.io/blog/dspm/full-data-scan-or-sampled
- elba data protection: https://documentation.elba.security/articles/data-protection
- GitGuardian incidents: https://docs.gitguardian.com/platform/getting-started/first-incident
- Macie findings and reveal: https://docs.aws.amazon.com/macie/latest/user/findings-retrieve-sd-proc.html
- RiskHarvester: https://arxiv.org/pdf/2502.01020
