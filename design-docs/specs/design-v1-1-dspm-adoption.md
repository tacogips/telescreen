# v1.1: Features Adopted from Comparable Sensitive-Data Monitoring Systems

Gap analysis of Telescreen v1 against the comparable systems surveyed in the owner's research
repository, and the specification of the features Telescreen adopts in v1.1. Owner request
(2026-10-09): research the similar systems and adopt the features worth adopting.

## Overview

Source research (public documents, fixed-commit source reads and papers, surveyed
2026-10-09), in `/Users/taco/gits/tacogips/ai-native-research/research/technology/`:

- `telescreen_related_systems_20261009.md` (summary of about 50 products, components and
  references)
- `telescreen_related_storage_monitoring_20261009.md` (DSPM: Sentra, Rubrik, IBM Guardium
  Exposure Manager, Concentric, Symmetry, Securiti, Cortex, Wiz, Netwrix, Datadog SDS)
- `telescreen_related_saas_dlp_20261009.md` (Teleskope, Nightfall, Strac, elba, Metomic, MIND,
  Polymer, Candor, Harmonic)
- `telescreen_related_enterprise_20261009.md` (Purview, Google SDP and Workspace DLP, Macie,
  BigID, Cyera, Varonis, Cyberhaven, Log360, P-Pointer, InterSafe PIS, Sumizumi-kun)
- `telescreen_related_oss_and_research_20261009.md` (GitGuardian, TruffleHog, Gitleaks,
  detect-secrets, DefectDojo, OpenMetadata, DataHub; IssueGuard, AssetHarvester,
  RiskHarvester, SecretBench)

Main finding: the closest products are DSPM tools (data security posture management). They
judge risk as **content x placement x exposure**, not content alone. Telescreen v1 judges
content only. The other recurring differentiators across the survey:

- issue identity and recurrence across copies;
- resolution backed by a re-scan, not by a manual state change;
- "unscanned is not safe";
- owner-driven remediation with deadlines;
- minimized evidence.

No surveyed product publicly guarantees all of them together. Telescreen v1 already covers
several, and v1.1 closes the remaining gaps.

### Already in v1 (no change needed)

| Capability | Seen in | Telescreen v1 |
|------------|---------|---------------|
| Sentry-style grouping, first/last seen, regression | Sentry, GitGuardian, DefectDojo | Issues with grouping key, statuses, `regressed` |
| Raw values never persisted; keyed fingerprints | TruffleHog Enterprise (claims), Metomic (hashing) | Masking pipeline, HMAC-SHA256, leak matrix tests |
| In-network scanning that pushes masked results only | Datadog SDS S3, Cyera Outpost, Symmetry, BigID scanners | Police workers (pull/push), SDK, n8n |
| Removal reconciliation only after a complete scan | DefectDojo `close_old_findings` pitfall, IBM | Sweeps reconcile only on `succeeded`; per-resource fetch failures block auto-resolve |
| Full + incremental scans, concurrent sweeps | Sentra (full/sampled, deltas), Macie | Event + schedule triggers, map-reduce sweeps |
| Owner-tier escalation, evidence, verification step | Teleskope wait nodes, Cyera escalation, IBM pending re-scan | Workflow engine: human tasks, SLA tiers, evidence gate, `police_verify`, `detect` |
| Outbound notification DLP | Workspace DLP snippet pitfall (evidence kept in alerts) | Outbound DLP gate on every notification |
| Pluggable detectors behind one interface | BigID, DataHub custom classifiers | Rule / ML / Claude or Codex / local LLM, cascade |

## Details

### 1. Adoption decisions

| # | Feature | Seen in | v1 gap | Decision |
|---|---------|---------|--------|----------|
| F1 | Exposure context (public link, external share, public channel or repo, Slack Connect) and exposure-aware severity | Sentra, Cyera, Varonis, Rubrik, Strac, elba, Wiz | Content only | **Adopt (v1.1)** |
| F2 | Placement policies: a rule class may be allowed in some locations (for example PII in the HR drive); policies are versioned; a policy change is not a remediation | Rubrik, IBM, Teleskope Policy Maker, DSPM in general | Every detection is a violation | **Adopt (v1.1)** |
| F3 | Resolution reasons and verified resolution (`pending_verification`: re-scan decides) | IBM (completed -> pending scan -> reopen), Rubrik and Teleskope pitfalls, BigID export-then-resolve pitfall | `manual` / `auto` only | **Adopt (v1.1)** |
| F4 | Per-resource scan coverage: scanned / partial / unsupported (encrypted, password-protected, image, too large) / permission denied / failed; "unscannable" list | Sentra coverage view, P-Pointer, Macie sampling | Not recorded | **Adopt (v1.1)** |
| F5 | Data-owner routing and self-service: assign to the resource owner (file owner, message author, CODEOWNERS); the owner chooses remove / restrict / business exception; weekly digest; "My tasks" | elba checklist, Teleskope owner DM, Cyera owner routing | Assignee is a team/member reference only | **Adopt (v1.1)** |
| F6 | Collector and Police health alerts: stale collector, expired watch channel, failing scans, silent Police; event loss repaired by sweeps | Macie EventBridge best-effort, survey guidance on delivery reliability | Police heartbeat only | **Adopt (v1.1)** |
| F7 | Cross-location secret linking: the same keyed fingerprint in several resources shows as linked occurrences; credential remediation covers all of them; fingerprint key versioning | GitGuardian (one incident per secret), survey HMAC/key-generation guidance | Grouping is per resource | **Adopt (v1.1)** |
| F8 | Risk score: severity x exposure x age x asset type | RiskHarvester (asset value and reachability), Wiz/Varonis prioritization | Severity only | **Adopt (v1.1)** |
| F9 | Exceptions with reason, approver and expiry; automatic reopen on expiry | elba business exceptions, IBM exception approval | `ignored` without expiry or approver | **Adopt (v1.1)** |
| F10 | Stale-data condition in policies (sensitive data older than N days) | Rubrik (stale), Teleskope (age), elba | None | **Adopt (v1.1)** as a policy condition of F2 |
| F11 | Ticket export (Jira, GitHub Issues, ServiceNow) with masked content; export never resolves | BigID, Cyera, Teleskope, Purview | None | **Adopt later (v1.2)** |
| F12 | Approval-gated automated remediation (restrict Drive sharing, revoke public link, hide or delete a Slack message, quarantine) | Strac, Teleskope, Varonis, Metomic, Cyera | Notify and verify only | **Rejected** (owner decision 2026-10-09): Telescreen stays read-only on every source; owners remediate, Telescreen verifies by rescan |
| F13 | Custom document classifiers for Japanese company secrets (labels and examples, LLM or local model) | Concentric, Cyera, MIND custom classifier | Rule classes are value types only | **Adopt later (v1.2)**; evaluated on the synthetic dataset |
| F14 | Credential validity check against the provider | TruffleHog, GitGuardian, detect-secrets | None | **Rejected** (owner decision 2026-10-09): no provider validity checks in any form |
| F15 | On-demand reveal of a matched value, fetched live from the source, never stored | Amazon Macie Reveal | None | **Rejected** (owner decision 2026-10-09): raw values are never revealed; deep links to the source location are allowed |
| F16 | Detector evaluation harness (precision/recall per class; notification volume diff on detector change) | Survey acceptance criteria, IssueGuard/SecretBench methodology | Planned in `design-synthetic-test-dataset.md` | Already planned; scheduled with F13 |
| R1 | Semantic grouping of similar documents, data lineage | Concentric, Cyberhaven | - | **Reject for now**: needs document embeddings or endpoint telemetry; out of scope |
| R2 | Endpoint and in-transit DLP, AI prompt inspection | Netskope, Forcepoint, Harmonic, Metomic (2026) | - | **Reject**: different product category |

### 2. F1 Exposure context

- **CollectorAdapter.** It returns an `Exposure` dataclass with each `ResourceContent`. The
  dataclass holds no names, only counts and enums:
  - `visibility`: `public`, `anyone_with_link`, `domain`, `external_shared`, `internal`,
    `private`, or `unknown`;
  - `external_principal_count`;
  - `shared_drive`;
  - `channel_kind`: `public`, `private`, `dm`, `connect`;
  - `repo_visibility`: `public`, `internal`, `private`;
  - `is_fork`.
- **Per source:**
  - Drive: `permissions.list` (types `anyone`, `domain`, `user`/`group` outside the
    configured internal domains).
  - Slack: conversation type and `is_ext_shared`.
  - GitHub: repository visibility and fork.
  - Police: the result envelope adds an optional `exposure` object (contract minor version).
- **Persistence:** the issue stores the latest `exposure_json` (canonical, enums and counts
  only) and `exposure_changed_at`. A history entry is written when exposure widens or
  narrows.
- **Severity:** `effective_severity = max(rule severity, exposure floor)`. `public` and
  `anyone_with_link` raise credentials to `critical` and PII to `high`. A new `critical`
  severity level is added.
- **UI:** an exposure badge in issue rows ("Public", "Shared externally (3)"), plus filters
  `exposure:` and `is:public`.

### 3. F2 Placement policies (with F10)

- **Model:** a new `placement_policies` table with versioned definitions in YAML or JSON:
  - `name`, `version`, `enabled`;
  - `match`: rule classes, sources, location selectors such as a Drive folder id, Slack
    channel id or repo glob;
  - `condition`: optional `max_exposure`, `max_age_days`;
  - `effect`: `allow` or `violate`;
  - `rationale` (masked text);
  - `owner_team`.
- **Evaluation:** the issue service evaluates policies at record time. An `allow` match
  creates or updates the issue as `allowed` (a new status). It stays visible in a separate
  view and doesn't notify.
- **Age condition:** `max_age_days` compares against the resource's last-modified time. Data
  older than the limit violates even in an allowed location.
- **Changing a policy:** this re-evaluates the open issues. Issues that become `allowed` get
  resolution reason `policy_changed`, never `remediated` (avoiding the Rubrik and Teleskope
  pitfall where disabling a policy closes violations). Policy versions and editors are
  audited.

### 4. F3 Resolution reasons and verified resolution

- **`IssueResolution` values:**
  - `verified_removed` (a rescan of the same resource found no finding);
  - `resource_deleted` (the source reports the resource gone);
  - `exposure_reduced` (the policy no longer violates after an exposure change);
  - `policy_changed`;
  - `false_positive` (requires a reason);
  - `accepted_risk` (from F9);
  - `manual_unverified`.
- **New status `pending_verification`:** a human "mark remediated" moves the issue there and
  enqueues a rescan of its resource.
  - The rescan outcome decides the result: clean resolves as `verified_removed`, a finding
    reopens the issue, and unreadable leaves it `pending_verification` with coverage state
    `unverifiable` and an alert (never resolved).
  - `manual_unverified` is allowed only for `admin`, with a reason, and is shown with a
    warning icon.
- **Workflow engine:** the `detect` node already performs the rescan, and its outcomes map
  onto these reasons.

### 5. F4 Scan coverage

- **Per-resource coverage in `ShardSummary` / `ResourceScan`:** `scanned`,
  `partial (truncated_bytes)`, `unsupported (reason: encrypted, password_protected, image,
  archive_too_deep, binary)`, `too_large`, `permission_denied`, `fetch_failed`.
- **Storage:** coverage is stored per scan job (`scan_coverage` counts by reason) and per
  resource for non-scanned states, in a `resource_coverage` table holding the keyed
  resource key, the reason and `last_attempt_at`. Display locators are masked.
- **UI:** a Collectors "Coverage" tab with counts and an "Unscannable resources" list.
  Unscannable resources never count as clean, and never auto-resolve issues (consistent
  with the sweep rule).
- **Policy:** `unsupported` resources in public exposure create an informational issue
  ("Unscannable file shared publicly"). This is configurable.

### 6. F5 Data-owner routing and self-service

- **Owner reference:** adapters return an `owner_ref`, a stable source user id reference
  resolved through a directory mapping, never stored as a raw email in issue rows:
  - Drive file owner;
  - Slack message author;
  - GitHub CODEOWNERS or the last committer, resolved through a configured mapping table
    from source user ids to members.
- **Assignment:** issues auto-assign to the mapped member when the policy allows. Otherwise
  they go to the policy `owner_team`, then to the default team.
- **Owner human task:** an `owner_review` task with outcomes `remove` (then F3
  verification), `restrict` (then verification of the exposure change), `exception` (F9,
  needs approval) and `not_mine` (reassigns to the team). It is delivered as a Slack DM or
  email through the DLP gate, falling back to email.
- **Digest and "My tasks":** a weekly digest notification lists each owner's open items
  (counts and masked titles only). A "My tasks" page shows the checklist.

### 7. F6 Collector and Police health

- **Health record per collector config:**
  - `last_success_at`;
  - `consecutive_failures`;
  - `watch_expires_at`;
  - `last_event_at`;
  - `expected_interval` (from cron).
- **Health alerts** (to the configured ops recipients through the DLP gate):
  - no successful scan within 2 expected intervals;
  - watch channel expiring within 24h without renewal;
  - more than N consecutive failures;
  - a Police worker without heartbeat for 5 minutes while tasks are queued.
- **Display:** Collectors and Police pages show health dots; a `health` endpoint feeds the
  dashboard.
- **Event loss:** event-triggered collectors with a schedule fallback reconcile missed
  events. The UI flags collectors that are event-only and have no schedule.

### 8. F7 Cross-location secret linking and key versioning

- **Linked occurrences:** findings already carry a keyed fingerprint. A `secret_occurrences`
  view groups open findings by fingerprint across resources (credential rule classes only by
  default).
- **Issue detail:** a "Linked occurrences (N)" section, with masked locators and links to
  the other issues.
- **Credential remediation:** a workflow option makes credential remediation cover all
  occurrences. Rotation is confirmed once; each location still needs `verified_removed`.
- **Key versioning:** fingerprints carry `key_version`. `TELESCREEN_FINGERPRINT_KEYS` holds
  versioned secret references. New findings use the active version. Linking across versions
  is not possible, so a key rotation starts new linking groups, documented in the UI.

### 9. F8 Risk score

- **Formula:** `risk = severity_weight x exposure_weight x age_factor x asset_factor`. It is
  an ordinal score from 0 to 100 for sorting, not a probability. The weights live in
  settings, with documented defaults.
  - `asset_factor` comes from the detector's asset type: cloud root credential > deploy
    token > personal token > PII class.
- **Use:** a sort option "Risk"; and escalation policies may use risk thresholds instead of
  severity. No provider validity signal is used (owner decision).

### 10. F9 Exceptions

- **Fields:** "Ignore" requires a reason (masked) and an expiry. Durations are 7, 30 or 90
  days, or "until regressed". "Forever" requires `admin`.
- **Approval:** exceptions created by owners need approval by the policy `owner_team` lead.
  This is a workflow human task.
- **Expiry:** on expiry the issue reopens with history `exception_expired`.

### 11. Implementation plan shape (v1.1 run)

This is the backlog for the next workflow run after v1 completes. It is not part of the v1
runs.

1. Vocabulary and data model: statuses `allowed` and `pending_verification`; resolution
   reasons; `critical` severity; exposure, coverage, policy, health and occurrence tables;
   Alembic migration.
2. Collector exposure and owner extraction (slack, google, github) plus a Police contract
   minor version.
3. Policy engine and evaluation in issue recording; policy API and UI editor.
4. Verified resolution flow and exceptions in the issue service and API.
5. Coverage recording in collectors and sweeps; coverage UI.
6. Owner routing, the `owner_review` task, digest notifications, the "My tasks" page.
7. Health monitor in the scheduler, plus alerts and UI.
8. Occurrence linking and fingerprint key versioning.
9. Risk score and sort; escalation by risk.
10. Leak-matrix extension: exposure, owner and coverage records must never contain raw
    values, file names or emails.

### 12. Owner decisions (resolved 2026-10-09)

- Credential validity checks: rejected (`design-docs/user-qa/qa-credential-validity-check.md`).
- On-demand reveal of raw values: rejected; deep links to the source location only
  (`design-docs/user-qa/qa-on-demand-reveal.md`).
- Write scopes and automated remediation: rejected; Telescreen is read-only on every source
  (`design-docs/user-qa/qa-automated-remediation-scopes.md`). F5 owner outcomes `remove` and
  `restrict` are actions the owner performs in the source; Telescreen only verifies them by
  rescan.

## References

Primary sources are cited in the research files listed in the Overview; key ones:

- IBM issue states: https://www.ibm.com/docs/en/exposure-manager?topic=managing-issues
- Rubrik policies: https://docs.rubrik.com/en-us/saas/dspm/dsp_policies.html
- Teleskope Policy Maker: https://docs.teleskope.ai/the-platform/policy-maker
- Nightfall deduplication: https://help.nightfall.ai/dashboard/sdp_events/deduplication
- Sentra coverage: https://sentra.io/blog/dspm/full-data-scan-or-sampled
- elba data protection: https://documentation.elba.security/articles/data-protection
- GitGuardian incidents: https://docs.gitguardian.com/platform/getting-started/first-incident
- Macie findings and reveal: https://docs.aws.amazon.com/macie/latest/user/findings-retrieve-sd-proc.html
- RiskHarvester: https://arxiv.org/pdf/2502.01020
