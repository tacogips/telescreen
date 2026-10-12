# v1.3: Sentry Parity for Triage and Response

This document specifies Telescreen v1.3. The owner asked (2026-10-12, translated): "Self-review
the UI and the features. Do we provide the same usability and functionality as Sentry? If
not, improve it." The accepted requirements are `design-v1-3-sentry-parity-review.md`
(defects B1-B7, gap table, scope S0-S10, deferrals, acceptance), as corrected in its
section 7.

Status: design accepted as the baseline for the `v13-pNN` plans (2026-10-12). On 2026-10-13
(run 2) section 6.1 was revised: the bulk `start`/`end`, the `params` keys and the
`issue_id` failure key now match the accepted client contract. Section 15 also gained the
run-2 shared-file ownership notes. On 2026-10-13 (run 3, base `ca818c3`) section 15 gained
the run-3 and finalization notes, and section 16 the live-check command. It extends the
implemented v1, v1.1 and v1.2 designs and replaces none of their decisions except those in
section 1. The summary tables are in `design-data-model.md` ("v1.3 additions"),
`design-api-and-ui.md` ("v1.3 endpoints"), `design-ui-sentry.md` section 11 and
`architecture.md` section 4 ("v1.3 additions").

## Overview

| # | Scope | Summary |
|---|-------|---------|
| S0 | Defects B1-B7 | Server-side event navigation, tags and stats; server query parsing; display names; persisted activity with actors |
| S1 | Issue stream | Status tabs with counts, period picker, per-issue trend, Values column, priority and trending sorts, display names, auto refresh banner |
| S2 | Search and views | Server-side query language (negation, `has:`, dates, comparisons, `priority:`, `assigned:none`, `assigned:#team`, `is:*`), autocomplete, server saved views |
| S3 | Bulk and merge | One bulk endpoint (ids or "all matching"), per-issue validation and audit; merge and unmerge |
| S4 | Archive and escalating | `archived` status with four conditions, all bounded by 90 days; `escalating` status; approved exceptions unchanged |
| S5 | Issue detail | Sentry-like header, event navigation API, tag distribution, per-issue stats, activity feed, comment edit and delete, mentions, subscriptions, bookmarks, external issue links, GitHub create |
| S6 | Alert rules | Rules with triggers, filters, actions, action interval, mute and history; they coexist with workflow triggers |
| S7 | Notifications | Per-user settings per category, subscription and mention delivery, weekly report |
| S8 | Dashboard | Overview stats with a period picker |
| S9 | Organization | Integrations page with tests, token UI (admin and self), member edit and deactivate, ownership rules |
| S10 | UX polish | Searchable palette, keyboard shortcuts, onboarding checklist, profile page, richer workflows list, realistic mock seed |

Baseline facts (checked against `main` at `57f389b`):

- `GET /api/issues` takes fixed parameters (`status`, `source`, `rule_class`, `severity`,
  `assignee`, `q`, `exposure`, `sort`). It filters with SQL and `LIKE` and does not use
  Meilisearch. The UI translates its query tokens into these parameters (`query.ts`).
- Issues group per (source, resource, rule class) (`issues/keys.py`).
- `IssueStatus` has `unresolved, acknowledged, in_progress, resolved, ignored, regressed,
  allowed, pending_verification`. `ignored` is reached only by an approved exception.
- Comments are `issue_history` rows of kind `commented` (redacted body in `note`, `actor_ref`).
- Cron work runs through `ScheduleProvider.tick(session, now)` (for example the owner digest
  in `issues/maintenance.py` with `croniter` and an idempotency key per fire).
- Workflow triggers are `IssueEventSink.on_issue_event`. `wiring.py` binds a
  `DeferredIssueEventSink` to `WorkflowTriggerService`. There are three filters (sources,
  rule classes, minimum severity).
- Sizes: `api/routers/issues.py` has 851 lines and `issues/service.py` has 978. Both are
  split in v1.3 (section 15).
- No new Python or UI dependency is needed. Charts are hand-rendered SVG (`Sparkline`,
  `MiniBarChart`).

## Details

### 1. Decisions

| # | Topic | Decision | Rationale |
|---|-------|----------|-----------|
| D1 | Open set | New constant `UNRESOLVED_STATUSES = unresolved, acknowledged, in_progress, regressed, escalating`, called "the open set" in this document. `is:unresolved` and the Unresolved tab mean the open set. `status:<value>` matches one status exactly. The existing internal `OPEN_STATUSES` (which includes `pending_verification`; used by reconciliation, policy apply, digest, exceptions and lifecycle) adds `escalating` and `archived`, so `RECONCILED_STATUSES` covers both, and the owner digest counts them | Sentry's Unresolved includes every unresolved substatus (fixes B3). The internal not-closed set is kept, so archived issues stay reconciled |
| D2 | New statuses | Add `archived` and `escalating` to `IssueStatus` (`String(32)`, no DB enum change). `ignored` stays the approved-exception state only | Archive and exceptions never share a state, so archive cannot become an exception |
| D3 | Archive bound | Every archive has `archive_until <= archived_at + 90 days`. Count and escalation conditions get `archived_at + 90 days`. When `archive_until` passes, the issue returns as `unresolved` and goes into For review. There is no "archive forever" | Requirement: archive never becomes permanent suppression |
| D4 | Escalation | An archived issue becomes `escalating` when its condition is met, or when its exposure widens during a scan. Section 7 gives the forecast | One deterministic, testable rule; widened exposure is never snoozed |
| D5 | Priority | New `priority` field (`high`, `medium`, `low`). The initial value comes from severity (`critical`/`high` -> `high`, `medium` -> `medium`) and follows severity until a user sets it (`priority_manual`). Severity and risk are unchanged | Sentry priority is a triage field and separate from detection severity |
| D6 | For review | New boolean `needs_review`. It is set on create, regression, escalation, system unarchive and verification failure. It is cleared by any human triage action (status, assign, archive, resolve, exception request, priority, mark reviewed). For review tab = open set AND `needs_review`. The old nav item "For review" (`assigned:me`) is renamed "Assigned to me" | Matches Sentry's For Review without a time heuristic |
| D7 | Query execution | The stream stays on SQL. A pure server-side parser (`telescreen.issues.query`) turns the query string into a typed AST and then a SQLAlchemy `Select`. Meilisearch keeps serving only `/api/search` | Exact counts and keyset paging; masked columns only; no index drift |
| D8 | Period | `period` = `all` (default), `24h`, `7d`, `14d`, `30d`, or `start`/`end` (custom, span <= 365 days). It scopes the list and counts to issues with `last_seen >= start` and `first_seen <= end`. The stream default is `all` | Divergence from Sentry's 14-day default: an old exposure stays exposed even when nothing new is seen |
| D9 | Values column | "Values" = `distinct_fingerprints` (distinct detected values, keyed fingerprints). The search key is `values:`. The archive condition is "until N more values". The alert trigger is "distinct values over an interval" | Review correction 7.2: every issue has one resource |
| D10 | Saved views | Server table, personal or shared, starred per member, and one default view per member. Name and query pass `Redactor`; a hit gives 422 `raw_value_detected` and nothing is stored. Each personal view needs a member principal | Views are persisted user text, so they need the same gate as templates (v1.2 3.4) |
| D11 | Bulk | `POST /api/issues/bulk` accepts either `ids` (<= 1000) or `query` + `period` (or `start`/`end`) ("select all matching", evaluated at request time, capped at 1000 with `truncated`). Each issue runs in its own transaction through the same service method as the single-issue route, with its own history and audit records, plus one `issue.bulk` audit summary | Partial success is explicit; there is no side door around per-issue validation |
| D12 | Merge model | A merge keeps the child rows. A child gets `merged_into_id` and is hidden from the stream, counts and tabs. Findings stay on the child. The primary's `times_seen`, `distinct_fingerprints`, `first_seen` and `last_seen` are recomputed over the group. Group actions cascade to the children. Child changes propagate to the primary (section 6.3). Unmerge clears `merged_into_id` | Per-resource verification, auto-resolve and exposure keep working; unmerge is exact |
| D13 | Merge preconditions | Every member is in the open set or `archived`. None has a `requested` or `active` exception. There is no nesting: a selected primary's children are re-parented. At most 50 children per primary. An exception request on any group member (a primary with children, or a merged child) is rejected (`merged_group`); unmerge first | Merge cannot bypass the exception approval flow |
| D14 | Comments | New `issue_comments` table (redacted body, author, edit and soft-delete timestamps). A new comment also writes a `commented` history row with `to_value = comment id` and `note = null`. Legacy `commented` rows are shown read-only as comments | Edit and delete need a mutable record; history stays append-only |
| D15 | Mentions | `@handle` tokens in the redacted body that match active members, at most 10 per comment. Only mentions added by a create or edit notify. A mentioned member is auto-subscribed | Sentry behavior; edits do not re-notify |
| D16 | Actor display | The server resolves `actor_ref` to `{kind, ref, display_name}`: `member:<id>` -> display name and handle; `token:<id>` -> token label; `system:*` -> "Telescreen". Lookup goes through a new `ActorDirectory` port | Fixes B4/B6 without exposing raw ids in the UI |
| D17 | External issues | Link by URL (https only, no userinfo, <= 2048 characters, `Redactor`-checked; a hit gives 422) for any tracker. GitHub create uses a dedicated token (`TELESCREEN_GITHUB_ISSUES_TOKEN`, a secret ref) and a repo allow-list (`TELESCREEN_GITHUB_ISSUES_REPOS`). The body is built only from allow-listed masked fields and passes `Redactor` and the DLP gate before egress | Least privilege: collector tokens stay read-only. The body is masked by construction |
| D18 | Alert rule targets | Actions use the v1.2 notification target shape inline (`recipients` x `channels` x optional `template` of event kind `alert_fired`) and an optional `start_workflow` name. They reuse the DSL v2 target validator | Notification sets live inside workflow documents; inline targets reuse the same rules |
| D19 | Alerts vs workflow triggers | Both evaluate the same issue events independently. A rule with an event trigger whose `start_workflow` names a workflow already triggered by the same event is rejected (`duplicate_trigger`) | No duplicate runs; existing workflows unchanged |
| D20 | Alert egress | `alerts.write` (create, edit, enable) is admin-only. `alerts.mute` is for responders and admins | Same egress control as workflow activation (v1.2 threat model) |
| D21 | Action interval | Per (rule, issue): after a `delivered` fire, later matches within `action_interval_minutes` record at most one `suppressed_interval` row per interval and send nothing | Sentry semantics; bounded history growth |
| D22 | Notification settings | Categories `assigned`, `mentions`, `subscribed`, `alerts`, `workflow_tasks`, `weekly_report`. Values `email`, `slack`, `off`. Direct categories (assigned, mentions, subscribed, weekly report) choose the channel; the default is email if the member has one, else Slack, else off. Rule- or workflow-addressed categories (`alerts`, `workflow_tasks`) act only as a filter on member recipients; with no row they are delivered as configured. `sla_escalated` is never filtered | Personal control without silencing the SLA safety net |
| D23 | Slack DM | Personal Slack delivery goes to `members.slack_user_id` through the existing `member:` resolution. The 2026-10-12 owner ruling (channel-only) applies to `slack_channel:` recipients and is unchanged | Member delivery already exists; the ruling is about workflow channel recipients |
| D24 | Weekly report | Cron `TELESCREEN_WEEKLY_REPORT_CRON` (default `0 9 * * 1` UTC; empty disables). Sent to each active member whose `weekly_report` setting is not `off`, through the editable template `system.weekly_report` | Reuses the owner digest pattern and the v1.2 templates |
| D25 | Ownership rules | Ordered rules (optional source + glob over `display_locator` -> team). Routing order: resource owner, first matching rule, policy team, default team. Only new routing uses rules; existing assignments are not rewritten. Patterns are `Redactor`-checked | Sentry ownership rules mapped to masked locators |
| D26 | Member deactivation | Sets `active = false` (last-admin rule applies). The member's open issues are released to the routing team. Bound tokens and sign-in stop authenticating. Reactivate restores `active` only | Unattended assignments are not left behind |
| D27 | Self tokens | Members create, list and revoke their own tokens. Scopes are limited by role (viewer `read`; responder `read`, `write`; admin `read`, `write`, `admin`). The token is always bound to the caller. The secret is shown once | Profile "my tokens"; the v1.2 intersection still applies |
| D28 | Onboarding | Client-only checklist over existing endpoints. An item whose endpoint the user cannot read is hidden. Dismissal is stored in `localStorage` | No new backend surface |
| D29 | Auto refresh | The UI polls `GET /api/issues/counts?...&updated_since=` every 30 s while the tab is visible. When new or updated issues exist it shows a banner and does not reorder the list | Sentry-like without push infrastructure |
| D30 | Integrations | The backend reports `slack`, `email`, `github_issues` and `oidc` status (booleans and enum codes only) and runs their tests. Collector integrations (Google, GitHub collectors, Police) are shown from the existing `/api/collectors` data | Smallest surface; no secrets or hosts returned |

### 2. Foundation: vocabulary, permissions, settings, ports

Enums in `telescreen.core.vocabulary` (persisted as strings):

| Enum | Change |
|------|--------|
| `IssueStatus` | adds `archived`, `escalating` |
| `IssueHistoryKind` | adds `archived`, `unarchived`, `escalated`, `priority_changed`, `reviewed`, `merged`, `unmerged`, `comment_edited`, `comment_deleted`, `external_issue_linked`, `external_issue_unlinked` |
| `IssueEventKind` / `RunTrigger` | add `issue_escalating`; `RunTrigger` also adds `alert_rule`. Workflows may trigger on `issue_escalating` |
| `IssuePriority` (new) | `high`, `medium`, `low` |
| `ArchiveKind` (new) | `until_escalating`, `for_duration`, `until_findings`, `until_values` |
| `AlertTriggerKind` (new) | `issue_created`, `issue_regressed`, `issue_escalating`, `frequency`, `values` |
| `AlertFireOutcome` (new) | `delivered`, `blocked`, `muted`, `suppressed_interval`, `failed` |
| `NotificationEvent` | adds `alert_fired`, `issue_assigned`, `issue_mentioned`, `issue_activity`, `weekly_report`, `integration_test` |
| `NotificationCategory` (new) | `assigned`, `mentions`, `subscribed`, `alerts`, `workflow_tasks`, `weekly_report` |
| `NotificationPreference` (new) | `email`, `slack`, `off` |
| `SavedViewVisibility` (new) | `personal`, `shared` |
| `SubscriptionReason` (new) | `manual`, `assigned`, `commented`, `mentioned` |
| `ExternalIssueKind` (new) | `url`, `github` |
| `IntegrationKind` (new) | `slack`, `email`, `github_issues`, `oidc` |
| `ErrorCode` | adds `invalid_query`, `recipient_opted_out`, `merged_group`, `dlp_blocked`, `integration_unconfigured`, `duplicate_trigger`, `limit_exceeded` |

`USER_TRANSITIONS` (`issues/transitions.py`) adds `escalating -> acknowledged, in_progress,
unresolved` and `archived -> unresolved` (unarchive). `PATCH /api/issues/{id}` still rejects
targets that have a dedicated transition (`resolved`, `ignored`), and now also rejects
`archived` and `escalating` with 422 `invalid_transition`.

Permissions (`core/permissions.py`). V = viewer, R = responder, A = admin. As in v1.2 D10,
the `read`, `write` and `admin` scopes map to the viewer, responder and admin sets.

| Permission | V | R | A | Routes |
|-----------|---|---|---|--------|
| `views.read` / `views.write` | x / x | x / x | x / x | list views / own personal views, stars, default view |
| `views.manage_shared` | | x | x | create, edit, delete shared views |
| `issues.follow` | x | x | x | bookmark, subscribe |
| `comments.manage_any` | | | x | edit or delete any comment |
| `external_issues.write` | | x | x | link, unlink, GitHub create |
| `alerts.read` / `alerts.write` / `alerts.mute` | x / / | x / / x | x / x / x | list, history / create, edit, enable, disable, delete / mute, unmute |
| `profile.self` | x | x | x | own notification settings and own tokens |
| `integrations.read` / `integrations.test` | | | x / x | status / test actions |
| `ownership.read` / `ownership.write` | x / | x / | x / x | list, test / create, edit, reorder, delete |

Existing permissions cover the rest: `issues.read` (counts, events selector, tags, stats,
activity, external links list, GitHub repo list, dashboard), `issues.write` (archive,
unarchive, priority, review, bulk, merge, unmerge, comment create and own edits),
`issues.resolve_unverified` (also checked once per bulk request with that reason, before any
change), `directory.write` (member edit), `users.write` (deactivate, reactivate),
`workflows.read` (enriched list). A route that needs a member principal (`views.*`,
`issues.follow`, `profile.self`) returns 403 `forbidden` for an unbound token. Comments
keep working for any principal, as in v1. The author is the caller's `actor_ref`, and
"own comment" means the same `actor_ref`.

Settings (`core/config.py`; all optional):

| Variable | Default | Meaning |
|----------|---------|---------|
| `TELESCREEN_ESCALATING_MIN_FINDINGS` | 10 | Floor of the 24 h escalation threshold (1-100000) |
| `TELESCREEN_ESCALATING_FACTOR` | 3 | Multiplier of the pre-archive daily peak (1-100) |
| `TELESCREEN_WEEKLY_REPORT_CRON` | `0 9 * * 1` | Weekly report cron (UTC); empty disables |
| `TELESCREEN_GITHUB_ISSUES_TOKEN` | unset | Secret ref (`env:`/`file:`) of the token used only for GitHub create and its test |
| `TELESCREEN_GITHUB_ISSUES_REPOS` | empty | Comma list `owner/repo` (pattern `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`, <= 20). GitHub create is enabled only when token and list are set |

GitHub create reuses `TELESCREEN_GITHUB_API_URL`.

Ports (`telescreen.ports`; DTOs are frozen dataclasses, interfaces are Protocols):

- `ports/issues.py`:
  - `FindingsObserver.on_findings_recorded(session, FindingsRecorded(issue_id,
    inserted, observed_at))`, called by the issue service after it inserts findings into an
    existing or new issue;
  - `IssueActivityNotifier.on_activity(session, IssueActivity(kind: IssueHistoryKind,
    issue_id, actor_ref, assignee_member_id | None, comment_id | None))`;
  - `IssueReportSource.weekly_summary(session, start, end) -> WeeklySummary`. Its fields are
    counts, `mttr_seconds`, and `top_issues: tuple[ReportIssue, ...]` (short id, masked
    title, findings);
  - `AssigneeSuggester.suggest(session, issue_id) -> tuple[SuggestedAssignee, ...]`, where
    `SuggestedAssignee(ref, reason)` and reason is `owner`, `ownership_rule` or
    `policy_team`. It is implemented with the ownership rules (p11) and used by the detail
    route;
  - `IssueSnapshot` gains `priority` and `assignee_team_slug`.
- `ports/notifications.py`:
  - `NotificationRequest` gains `category: NotificationCategory | None = None`;
  - `NotificationSender.send_to_member(session, MemberNotification(member_id, category,
    event, context, issue_id)) -> NotificationReceipt`;
  - `NotificationContext` gains the alert, activity, comment and report fields of section
    10.3;
  - `NotificationTester.send_test(session, channel, member_id) -> TestOutcome`;
  - `OutboundContentGuard.inspect(session, text, purpose) -> GuardVerdict`. This is the
    notification DLP gate exposed for non-notification egress (GitHub create).
- `ports/workflow.py`: `RunStarter.start_for_issue(session, workflow_name, issue_id,
  trigger) -> str | None` returns `None` when there is no active version or a run is already
  running or waiting for that issue. `RunStarter.trigger_events(session, workflow_name) ->
  tuple[IssueEventKind, ...] | None`.
- `ports/directory.py`: `ActorDirectory.display(session, refs: tuple[str, ...]) ->
  tuple[ActorDisplay, ...]` where `ActorDisplay(ref, kind, display_name, handle | None,
  active)`. Unknown refs come back as kind `unknown` with the display name "Unknown".

Wiring binds no-op defaults until the wiring plan binds the real implementations (v1.1/v1.2
rule). `IssueEventSink` becomes a fan-out (`workflow triggers`, `alert evaluator`) built in
`wiring_v13.py`.

### 3. S0 Defect fixes

| Id | Fix | Regression test (fails on `57f389b`) |
|----|-----|--------------------------------------|
| B1 | `GET /api/issues/{id}/events/{selector}` (section 8.2) orders by (`observed_at`, `id`) ascending: `previous` = older, `next` = newer. The UI buttons are Oldest, Previous, Next, Latest and Recommended, and use the returned ids only | API: with 3 findings at t1 < t2 < t3, `oldest` returns t1 with `next_id` = t2. UI: "Oldest" selects t1 |
| B2 | `assigned:none` (and the legacy `assignee=none`) becomes `assignee_member_id IS NULL AND assignee_team_id IS NULL` and never reaches the resolver | API: `assignee=none` returns 200 with only unassigned issues |
| B3 | `is:unresolved` = open set (D1); the default stream is the Unresolved tab | API and UI: a `regressed` and an `acknowledged` issue appear under `is:unresolved` |
| B4 | List and detail responses carry `assignee: {kind, ref, display_name} \| null`. The UI renders initials avatars and names everywhere, including the header picker | UI: the list cell and the picker show "Alice Example", never `mem-01` |
| B5 | The server parses negation for every key. The UI sends the raw query string and never drops tokens | API: `!source:slack` excludes Slack issues. UI: the request carries `query` with the negated token |
| B6 | Activity feed API with actors (section 8.4). The UI drops the session-local echo and refetches after a post | UI: after reload, a posted comment shows its body and author; the timeline shows actors |
| B7 | Navigation, chart and tags use the selector, stats and tags endpoints over all findings of the issue or group | API: 120 findings give `total = 120` and tag counts over 120. UI: the chart uses `/stats` |

### 4. S1 Issue stream

`GET /api/issues` gains:

- `query` (section 5), `period` / `start` / `end` (D8), and `trend` = `24h` | `14d` | `none`
  (default `14d`);
- `sort` = `last_seen` (default), `first_seen`, `times_seen`, `values`, `risk`, `priority`
  (rank, then `last_seen`) or `trending` (findings in the last 24 h, then `last_seen`). The
  keyset cursor encodes the sort tuple and the id.

Legacy parameters stay accepted and are combined with AND. Merged children
(`merged_into_id IS NOT NULL`) are always excluded.

New item fields: `priority`, `priority_manual`, `needs_review`, `assignee` (B4), `archive`
(`{kind, until, threshold, baseline}` or null), `merged_count`, `bookmarked` (caller),
`updated_at`, and `trend` (24 hourly or 14 daily zero-filled counts over the group findings,
computed with one grouped query for the page).

`GET /api/issues/counts?query&period&start&end&updated_since` returns `{tabs: [{tab,
count}], updated_since_count}`. Tabs are computed from the query without its `is:` tokens,
intersected with each tab filter:

| Tab | Filter |
|-----|--------|
| `unresolved` | open set |
| `for_review` | open set AND `needs_review` |
| `regressed` | `status = regressed` |
| `escalating` | `status = escalating` |
| `archived` | `status = archived` |
| `resolved` | `status = resolved` |

`updated_since_count` counts matching issues with `updated_at > updated_since` (D29). The
statuses `pending_verification`, `ignored` and `allowed` keep their left-nav entries
(Verifying, Allowed) and are reachable with `is:`.

UI columns: select, issue (title, short id, source, rule class, badges), trend
(`Sparkline`), events (`times_seen`), values (`distinct_fingerprints`), priority
(`PriorityBadge` from `priority`), risk, assignee (avatar plus name), last seen and age.
There is a 24h/14d trend toggle, a period picker (all, 24h, 7d, 14d, 30d, custom), and sort
options for every `sort` value.

### 5. S2 Search and saved views

#### 5.1 Query language (`telescreen.issues.query`)

Tokens are `key:value`, `!key:value`, `key:[a,b]` (OR), quoted values, and free text (words
or quoted phrases). There are at most 20 tokens and 1000 characters.

| Key | Values |
|-----|--------|
| `is` | `unresolved` (open set), `for_review`, `regressed`, `escalating`, `archived`, `resolved`, `pending_verification`, `ignored`, `allowed`, `acknowledged`, `in_progress`, `assigned`, `unassigned`, `public` (legacy: exposure public) |
| `status` | any `IssueStatus` (exact) |
| `severity`, `priority`, `rule`, `source`, `exposure` | enum values |
| `assigned` | `me`, `my_teams`, `none`, `member:<handle>`, `team:<slug>`, `#<slug>` |
| `has` | `assignee`, `owner`, `exception`, `external_issue` |
| `first_seen`, `last_seen` | `-24h` / `-7d` / `-2w` (within), `+7d` (older than), `>`/`>=`/`<`/`<=` with an ISO date or date-time, or a plain date (that UTC day) |
| `times_seen`, `values` | `N`, `>N`, `>=N`, `<N`, `<=N` |
| `bookmarks` | `me` |

Free text: `TELE-<n>` matches the issue number exactly. Any other text is a
case-insensitive `LIKE` over `display_title` and `display_locator`, with `%` and `_`
escaped. Errors give 422 `invalid_query` with `{token_index, reason}` (`unknown_key`,
`invalid_value`, `too_many_tokens`, `too_long`). Error responses never echo the query.

Autocomplete runs in the client. It is built from the key spec (shipped in `query.ts`),
enum values, and the members and teams lists already loaded from the directory API. No new
endpoint is needed.

#### 5.2 Saved views

`saved_views` (section 13.1). API:

- `GET /api/views` returns the caller's personal views and all shared views, with `starred`
  and `is_default` for the caller;
- `POST /api/views`, `PATCH /api/views/{id}` and `DELETE /api/views/{id}`. The owner may
  change a personal view; shared views need `views.manage_shared`;
- `PUT` and `DELETE /api/views/{id}/star`;
- `PUT /api/views/default {view_id | null}`.

Validation: name 1-64 characters; the query parses; `sort` and `period` are valid; at most
50 personal views per member and 100 shared views (`limit_exceeded`); `Redactor` is applied
to name and query (D10). Audit actions are `view.created`, `view.updated` and
`view.deleted`, with the id and visibility only. The UI imports the old `localStorage` views
once (one `POST` each; a failed view stays local and is reported in a toast).

### 6. S3 Bulk actions and merge

#### 6.1 Bulk

`POST /api/issues/bulk {ids? | query + (period? | start + end), action, params}`. The
query-mode scope uses the same `parse_period(period, start, end, now)` rules as
`GET /api/issues` (D8): `start`/`end` go together with `period` absent or `custom`, and the
span is at most 365 days; otherwise the request is rejected with `VALIDATION_ERROR` before
any change. `params` is one object whose keys follow the accepted client contract
(`BulkParams` in `src/web/src/api/types.ts`):

| Action | `params` keys |
|--------|---------------|
| `status` | `status` |
| `assign` | `assignee_ref` (null unassigns) |
| `priority` | `priority` |
| `archive` | `archive {kind, duration_minutes?, count?}` (section 7) |
| `unarchive` | none |
| `resolve` | `reason`, `note` |
| `mark_reviewed` | none |
| `request_verification` | `verification {action, include_linked}` |

The action's parameters are parsed and validated once, before the first issue is touched.
A missing or invalid parameter rejects the whole request with 422 `VALIDATION_ERROR` and
changes nothing. `archive` is parsed into the same `ArchiveRequest` and checked by the same
`validate_archive_request` as the single-issue route: `kind` is required and never
defaulted, `until_escalating` takes neither `duration_minutes` nor `count`, `for_duration`
takes only `duration_minutes` (30-129600), and the count kinds take only `count`
(1-100000).

The existing per-issue "Start workflow" bulk action is not part of this endpoint. It stays
a client loop over `POST /api/workflows/{name}/runs`, because each run is already an
audited, per-issue operation. Every other stream bulk action uses this endpoint when
"Select all N matching" is active. That includes "Mark remediated", which is
`request_verification` with `verification {action: "remove", include_linked: false}`. The
UI sends `start`/`end` whenever the stream period is `custom`.

The response is `{matched, processed, truncated, succeeded: [issue_id], failed:
[{issue_id, code}]}`. Search indexing runs after each issue commit. An unknown or
invisible id becomes a `not_found` failure.

#### 6.2 Merge and unmerge

`POST /api/issues/merge {primary_id, ids}` checks D13 and then, in one transaction:

1. sets `merged_into_id` on each child (and re-parents grandchildren);
2. copies the primary's lifecycle state (status, archive fields, assignee) to each child;
3. writes one `issue_merges` row per child and `merged` history on both sides;
4. recomputes the group counters on the primary;
5. if the primary is `archived`, rebases its archive state to the new group scope with
   `rebase_archive(issue, scope)` in `issues/archive.py` (defined below).

`POST /api/issues/{primary_id}/unmerge {ids}` clears `merged_into_id` and sets
`unmerged_at`. Each child keeps its current status and gets `needs_review = true`. The
primary's counters are recomputed. Then `rebase_archive` runs for each `archived` child
(scope: the child's own findings) and for the primary when it is `archived` (scope: the
remaining group). History `unmerged` is written on both sides. Audit actions are
`issue.merged` and `issue.unmerged` (ids and counts only).

`rebase_archive(issue, scope)` changes only `archive_baseline` and `archive_threshold`.
`archive_kind`, `archived_at` and `archive_until` are kept, so the D3 90-day bound is never
extended. By kind:

| Kind | Rebase |
|------|--------|
| `until_findings` | `archive_baseline` = the recomputed `times_seen` of the scope. `archive_threshold` is kept, so N new findings are still needed |
| `until_values` | `archive_baseline` = the recomputed `distinct_fingerprints` of the scope; `archive_threshold` kept |
| `until_escalating` | `archive_threshold = max(MIN_FINDINGS, FACTOR * P)`, where `P` is the larger of: the highest UTC-day finding count of the scope in the 7 days before the rebase, and the scope's findings in the trailing 24 h at rebase time |
| `for_duration` | no change |

Neither merge nor unmerge runs archive evaluation. Evaluation runs only at the next ingest
or tick (section 7).

#### 6.3 Group coordination (`issues/groups.py`)

- **Cascade**: on a primary with children, these actions apply to every child (with a
  history note `via TELE-<n>`): status change, assign, archive, unarchive, reasoned resolve,
  request verification, mark reviewed. A transition that is invalid for a child skips that
  child and records it in the bulk or route result.
- **Propagation**: when a child's status becomes open through ingestion or verification
  failure, the primary changes as follows, with history `via TELE-<child>`, needs review,
  and the matching issue event:
  - `resolved` -> `regressed`;
  - `pending_verification` -> `unresolved`;
  - `archived` -> `escalating`.
- **Archive evaluation** for a group runs on the primary with group counters (section 7).
- **Views**: events, tags, stats and trend of a primary cover the group. `GET` of a child
  returns `merged_into: {id, short_id}`.

### 7. S4 Archive and escalating

`POST /api/issues/{id}/archive {kind, duration_minutes?, count?}` is allowed from the open
set. `POST /api/issues/{id}/unarchive` goes from `archived` to `unresolved`.

| Kind | Parameters | Stored | Leaves `archived` when |
|------|-----------|--------|------------------------|
| `for_duration` | `duration_minutes` 30-129600 | `archive_until = now + d` | `archive_until` passes -> `unresolved` |
| `until_findings` | `count` 1-100000 | `archive_baseline = times_seen`, `archive_threshold = count` | group `times_seen - baseline >= threshold` -> `escalating` |
| `until_values` | `count` 1-100000 | `archive_baseline = distinct_fingerprints`, `archive_threshold = count` | group `distinct_fingerprints - baseline >= threshold` -> `escalating` |
| `until_escalating` | none | `archive_threshold = max(MIN_FINDINGS, FACTOR * peak)` where `peak` is the highest UTC-day finding count of the group in the 7 days before archiving | group findings with `observed_at > now - 24h` reach `archive_threshold` -> `escalating` |

Every kind also:

- sets `archive_until <= archived_at + 90 days` (D3) and returns to `unresolved` when it
  passes;
- escalates when a scan widens the exposure (`visibility_rank` grows) while the issue is
  archived (D4).

Evaluation runs in the ingest transaction (`issues/archive.py`, called from the issue
service after findings are inserted). Expiry runs in `ArchiveMaintenanceProvider.tick`
(every minute; history actor `system:archive`). Leaving `archived` clears the archive fields,
sets `needs_review`, writes `escalated` or `unarchived` history, and emits
`issue_escalating` when escalating.

Safety rules (each has a test):

1. No v1.3 path writes `ignored`, `allowed` or an exception row. Exception requests from
   `archived` use the unchanged v1.1 flow; approval moves the issue to `ignored` and clears
   the archive fields.
2. Archived and escalating issues stay in verification (`request_verification` accepts
   both), auto-resolve, exposure tracking and policy re-evaluation (`OPEN_STATUSES`, and so
   `RECONCILED_STATUSES`, add both; D1).
3. `archive_until` is never null for an archived issue and never later than 90 days after
   archiving.
4. Every transition out of `archived` clears the archive fields through one helper in
   `issues/archive.py`. This includes policy re-evaluation to `allowed`, verification
   requests, auto-resolve and exception approval.
5. Merge and unmerge never trigger escalation by themselves and never extend
   `archive_until` (`rebase_archive`, section 6.2). After a rebase, only findings, values or
   exposure changes that arrive later can escalate the issue. Test owners: p05 unit-tests
   `rebase_archive` for every kind, including a check that `archive_until` and
   `archived_at` do not change. p06 tests the merge and unmerge paths (archived primary,
   archived child after unmerge, no `escalating` transition and no `issue_escalating`
   event from the merge or unmerge transaction).

### 8. S5 Issue detail

#### 8.1 Header and detail fields

Header layout:

- a **Resolve** split button: resolve with reason, request verification (remove or
  restrict, with linked occurrences), mark reviewed;
- an **Archive** split button: until escalating; for 1 h, 24 h, 7 d, 30 d or 90 d; until N
  more findings; until N more values;
- an assignee picker with suggested owners first;
- an editable priority;
- bookmark and subscribe toggles;
- copy permalink and copy masked Markdown;
- a **More** menu: request exception, start workflow, upload evidence, link or create an
  external issue, merge with, unmerge.

`GET /api/issues/{id}` adds:

- the S1 item fields;
- `subscription: {subscribed, reason}`;
- `merged_into` and `merged_children` (`[{id, short_id, source, display_locator,
  status}]`);
- `suggested_assignees: [{ref, display_name, reason}]`, where reason is `owner`,
  `ownership_rule` or `policy_team`.

`PATCH /api/issues/{id}` accepts `priority`. `POST /api/issues/{id}/review` clears
`needs_review`.

#### 8.2 Event navigation

`GET /api/issues/{id}/events/{selector}`, where selector is `oldest`, `latest`,
`recommended` or a finding id. It returns `{event, previous_id, next_id, position, total}`
over the group, in chronological order. `recommended` is the highest `confidence`, then the
latest. `GET /api/issues/{id}/events` gains `order=asc|desc` (default `desc`, unchanged).
Each event item adds `issue_short_id` (it differs for merged children).

#### 8.3 Tags and stats

- `GET /api/issues/{id}/tags?period` returns `{total, tags: [{key, values: [{value, count,
  percent}], other_count}]}`. Keys are `detector`, `rule_id`, `location` (the first location
  segment split on `[ >/:]`, as the UI does today), and, for groups only, `source` and
  `member` (short ids). It returns the top 10 values; percentages have one decimal.
- `GET /api/issues/{id}/stats?period&bucket=hour|day` returns zero-filled buckets over the
  group. The default is 14 days by day.

#### 8.4 Activity, comments, mentions, subscriptions, bookmarks

- `GET /api/issues/{id}/activity?cursor` returns newest first. Each item is `{id, type:
  history|comment, kind, actor: ActorDisplay, at, from_value, to_value, note, comment: {id,
  body, edited_at, deleted, can_edit} | null}`.
  - Assignment values (`member:`/`team:` refs) are rendered with display names.
  - `commented` history rows that point to a comment are replaced by the comment item.
- Comment routes:
  - `POST /api/issues/{id}/comments` (unchanged path) creates an `issue_comments` row;
  - `PATCH` and `DELETE /api/issues/{id}/comments/{comment_id}` are allowed for the author,
    or with `comments.manage_any`.

  The body is redacted, then must be 1-4000 characters. Delete is soft: the body is
  cleared, `deleted_at` is set, and `comment_deleted` history is written. Mentions follow
  D15.
- Subscriptions:
  - `PUT /api/issues/{id}/subscription {subscribed}` (`issues.follow`) stores the choice
    with reason `manual`;
  - an explicit unsubscribe is kept, and later auto-subscribe does not override it;
  - auto-subscribe happens on member assignment, comment and mention.
- Bookmarks: `PUT` and `DELETE /api/issues/{id}/bookmark`. Query with `bookmarks:me`.
- Activity notifications: `IssueActivityNotifier` (implemented with the subscriptions)
  receives these kinds:

  | Kind | Emitted by |
  |------|-----------|
  | `commented` | comments (p07) |
  | `status_changed` (including reasoned resolve), `assigned`, `archived`, `unarchived`, `merged`, `unmerged` | lifecycle and groups (p05, p06) |
  | `regressed`, `escalated`, `auto_resolved` | ingest and maintenance (p05) |

  Recipients:
  - a newly assigned member gets one `assigned` notification;
  - every other subscriber except the actor gets one `subscribed` notification;
  - both go through `send_to_member`.

#### 8.5 External issues

- `GET /api/issues/{id}/external-issues` lists links.
- `POST /api/issues/{id}/external-issues {url, title?}` creates a link (D17; unique per
  issue and URL).
- `DELETE /api/issues/{id}/external-issues/{link_id}` removes one.
- `GET /api/external-issues/github/repos` returns `{configured, repos}`.

GitHub create is `POST /api/issues/{id}/external-issues/github {repo, title?}`:

1. The repo must be in the allow-list (`validation_error`); without configuration the route
   returns 503 `integration_unconfigured`.
2. The title defaults to `<short_id>: <display_title>`. A user title passes `Redactor`; a
   hit gives 422.
3. The body is a fixed text built only from these allow-listed fields: short id, rule class,
   severity, priority, exposure visibility, masked location, latest masked snippet, and the
   Telescreen link (`TELESCREEN_PUBLIC_BASE_URL`).
4. Title and body pass `Redactor` and then `OutboundContentGuard`. A block gives 422
   `dlp_blocked`, nothing is sent, and the audit records `external_issue.blocked`.
5. `POST {api}/repos/{repo}/issues` uses the resolved token (fixed timeout; `httpx`; tests
   use `MockTransport`). A provider error gives 502 `provider_error`.
6. The link row stores `html_url` and `external_ref = owner/repo#n`. The audit records
   `external_issue.created` (repo and number only).

### 9. S6 Alert rules (`telescreen.alerts`, new L3 package)

Rule fields:

- `name` (slug-safe, 1-64 characters, unique, passes `Redactor`) and `enabled`;
- `trigger {kind, threshold?, window_minutes?}`. `frequency` and `values` need a
  threshold of 1-100000 and a window of 5-10080 minutes;
- `filters {min_severity?, priorities[], sources[], rule_classes[], exposures[],
  owner_teams[], detectors[]}`;
- `actions {targets[0..10], start_workflow?}`. At least one action is required;
- `action_interval_minutes` (5-43200, default 60);
- `muted_until` (at most 90 days ahead).

Target validation reuses the DSL v2 target rules (recipient kinds, channel combinations).
A target template must exist with event kind `alert_fired` (`TemplateCatalog`).
`start_workflow` must name a workflow with an active version (`RunStarter.trigger_events`)
and must satisfy D19.

API (`alerts.read` for GET; mute and unmute need `alerts.mute`; everything else needs
`alerts.write`):

- `GET` and `POST /api/alert-rules`; `POST /api/alert-rules/validate`;
- `GET`, `PUT` and `DELETE /api/alert-rules/{id}`;
- `POST /api/alert-rules/{id}/{enable|disable|mute|unmute}`. Mute takes `{minutes}`;
- `GET /api/alert-rules/{id}/history?cursor`.

List items add `last_fired_at`, `fires_24h` and a status (`enabled`, `disabled`, or
`muted` until a time).

Evaluation (`AlertEvaluator`, bound as an `IssueEventSink` and a `FindingsObserver`; it runs
in the ingest or lifecycle transaction):

1. Event triggers match on `IssueEvent.kind`. `frequency` counts the findings of the group in
   `[now - window, now]`; `values` counts their distinct fingerprints. Archived issues
   (and merged children) never match `frequency` or `values`. A child event is evaluated
   against its primary.
2. Filters run against the `IssueSnapshot`. `owner_teams` matches the assignee team slug
   (`IssueSnapshot.assignee_team_slug`). Ownership rules affect it through routing.
3. The outcome is decided in this order:
   - disabled: no fire;
   - muted: one `muted` fire (at most one per interval, as in D21);
   - inside the interval: `suppressed_interval` (D21);
   - otherwise: one `NotificationRequest` per target and channel (event `alert_fired`,
     category `alerts`), plus `RunStarter.start_for_issue(..., RunTrigger.alert_rule)`.
4. The fire row records the outcome:
   - `delivered` when at least one notification is queued or a run starts;
   - `blocked` when every notification is DLP-blocked;
   - `failed` on an internal error, with the error code.

   `notification_count`, `run_id` and `last_fired_at` are updated.
5. `AlertMaintenanceProvider` deletes fires older than 90 days once a day.

The workflows list (`GET /api/workflows`) adds `trigger_events`, `active`, `last_run_at`
and `runs_30d {running, waiting, succeeded, failed, cancelled}`.

### 10. S7 Notifications

#### 10.1 Settings

`notification_settings` (member, category) rows (D22). The API is `GET` and `PUT
/api/auth/me/notification-settings` (`profile.self`). It returns each category with its
stored value (or null), the effective channel, and whether the member has an email address
and a Slack id.

`NotificationService` applies the settings:

- `send_to_member` reads the setting. With `off`, or no address on the chosen channel, it
  writes one `skipped` row (`recipient_opted_out` or `recipient_unreachable`). Otherwise it
  renders the event template and sends through the normal DLP path.
- `send` with `category` `alerts`, or with event `human_task_assigned` or
  `owner_review_assigned` (category `workflow_tasks`), filters member recipients. With
  `off`, or a stored channel different from the request channel, the member gets a
  `skipped` row with `recipient_opted_out`. Team, email, Slack and Slack-channel
  recipients are not affected.
- `notifications.category` is stored on every v1.3 row.

#### 10.2 Weekly report

`WeeklyReportProvider` (a `ScheduleProvider`) enqueues task `weekly_report` with idempotency
key `weekly:<fire minute>` at each cron fire. The handler:

1. reads `IssueReportSource.weekly_summary(prev fire, fire)` once;
2. sends `send_to_member(category weekly_report, event weekly_report)` to each active
   member, with `report.summary` and `report.assigned_open` (open issues assigned to that
   member).

The summary contains the new, resolved, regressed and escalating counts, the open total,
MTTR, and the top 5 issues by findings (short id, masked title, count).

#### 10.3 Built-in templates and context

Built-ins `system.<event>` for the six new events:

| Event | Allowed variable groups |
|-------|-------------------------|
| `alert_fired` | issue, `alert.rule`, `alert.trigger`, `alert.count`, `alert.window` |
| `issue_assigned` | issue, `assignee.ref`, `activity.actor` |
| `issue_mentioned` | issue, `activity.actor`, `comment.excerpt` |
| `issue_activity` | issue, `activity.kind`, `activity.actor`, `comment.excerpt` |
| `weekly_report` | `report.summary`, `report.assigned_open` |
| `integration_test` | `health.target` |

- `comment.excerpt` is the first 200 characters of the redacted comment.
- `activity.actor` is a display name.
- Every value comes from masked sources, as in v1.2 3.3, and passes `Redactor` and DLP at
  send.

### 11. S8 Dashboard

`GET /api/dashboard?period=24h|7d|14d|30d|90d|start,end` (`issues.read`; default `14d`)
returns:

- `buckets: [{start, critical, high, medium, new_issues, resolved_issues}]` (hourly for
  `24h`, daily otherwise). The severity counts are findings in the bucket, grouped by the
  current severity of their issue. `new_issues` counts `first_seen` in the bucket and
  `resolved_issues` counts `resolved_at` in the bucket;
- `open_by_exposure` and `open_by_source`: current open-set counts, excluding merged
  children;
- `archived_count` and `escalating_count`;
- `top_issues`: the top 10 by findings in the period (`id`, `short_id`, `display_title`,
  `findings`);
- `mttr_seconds`: the mean of `resolved_at - first_seen` for issues resolved in the period;
- `mtta_seconds`: the mean time from `first_seen` to the first non-system `status_changed`,
  `assigned` or `commented` history row, for issues first seen in the period;
- `unassigned_high_priority`: open, unassigned and `priority = high`.

Each null mean means there is no data. Every tile links to a stream query.

### 12. S9 Organization

- **Integrations**:
  - `GET /api/integrations` (`integrations.read`) returns `[{kind, configured,
    last_check_at, last_check_ok, error_code, docs_path}]`;
  - `POST /api/integrations/{kind}/test` (`integrations.test`) runs the test:
    - `slack` and `email`: `NotificationTester.send_test` to the calling member on that
      channel, delivered inline like `telescreen notify test`;
    - `github_issues`: `GET /repos/{repo}` for each configured repo;
    - `oidc`: discovery and JWKS refresh through `BearerVerifier.metadata()`.

    Results are stored in `integration_checks`. No token, host or address is returned.
- **Tokens**:
  - admin `/settings/tokens` uses the v1.2 routes;
  - self routes `GET` and `POST /api/auth/me/tokens` and `POST
    /api/auth/me/tokens/{id}/revoke` (`profile.self`) follow D27;
  - the audit records `token.created` and `token.revoked` without the value.
- **Members**:
  - `PATCH /api/members/{handle} {display_name?, email?, slack_user_id?}`
    (`directory.write`). The email follows the `email:` recipient rules; the Slack id
    pattern is `^[UW][A-Z0-9]{8,}$`. Audit `member.updated` with field names only.
    OIDC-linked members may have their profile fields refreshed at sign-in, as in v1.2;
  - `POST /api/members/{handle}/deactivate` and `/reactivate` (`users.write`) follow D26.
    Audit `member.deactivated` and `member.reactivated`.
- **Ownership rules** (D25):
  - `GET` and `POST /api/ownership-rules`;
  - `PUT` and `DELETE /api/ownership-rules/{id}`;
  - `POST /api/ownership-rules/reorder {ids}`;
  - `POST /api/ownership-rules/test {source?, locator}`, which returns `{rule_id, team}` or
    nulls.

  Patterns are globs (`*`, `?`, `**`), at most 512 characters, case-sensitive, matched
  against `display_locator`. Pattern and test locator pass `Redactor` (422, no echo).
  `IssueOwnerRouter` inserts the first matching enabled rule after the resource owner.

### 13. S10 UX polish and mock seed

- **Palette** (Cmd/Ctrl+K):
  - it searches pages and actions locally;
  - it searches issues through `GET /api/issues?query=<text>&limit=8`, where a `TELE-n`
    input matches exactly;
  - Enter opens the selected entry.
- **Shortcuts**:
  - stream: `j`/`k` move, `o`/Enter open, `x` select, `r` resolve dialog, `a` assign picker,
    `e` archive menu, `/` focus search;
  - detail: `r`, `a`, `e`, `b` bookmark;
  - `?` shows the help overlay;
  - shortcuts are disabled while focus is in an editable element.
- **Onboarding** (D28): a checklist with these items:
  - connect a collector (`/api/collectors`);
  - configure notifications (`/api/integrations`);
  - invite users (`/api/members`, more than one active);
  - create an alert rule (`/api/alert-rules`).

  It is shown on the dashboard and in the empty stream until it is complete or dismissed.
  Docs links point to README sections.
- **Profile** `/settings/profile`: notification settings, my tokens, default view, theme
  (client).
- **Workflows list**: name, trigger events, active, last run, 30-day run counts.
- **Mock seed** (`src/web/src/api/mock/fixtures`):
  - at least 40 issues, with at least 2 in each of the 10 statuses;
  - all 7 sources, 3 severities, 3 priorities and 7 exposures;
  - at least 5 assignees (members and teams);
  - comments with mentions, a merged group, external links;
  - at least 3 alert rules with history;
  - notification settings, tokens, ownership rules and integration statuses.

  Values use Masker formats and reserved example names only;
  `tests/leak/test_ui_fixtures.py` must keep passing.

#### 13.1 Data model (revision `0004_v1_3`; summary in `design-data-model.md`)

`issues` gains these columns:

- `priority` (`String(16)`, server default `medium`; the migration backfills from severity)
  and `priority_manual` (bool);
- `needs_review` (bool; backfilled true for `unresolved` and `regressed`);
- `merged_into_id` (`String(36)`, indexed);
- `archive_kind`, `archive_until`, `archive_threshold`, `archive_baseline` and
  `archived_at` (all nullable).

`notifications` gains `category`. New tables:

| Table | Columns | Owner package |
|-------|---------|---------------|
| `saved_views` | id, name, query, sort, period, visibility, owner_member_id, position, created_at, updated_at | issues |
| `saved_view_stars` | view_id, member_id (PK) | issues |
| `saved_view_defaults` | member_id (PK), view_id | issues |
| `issue_comments` | id, issue_id (idx), author_ref, body, created_at, edited_at, deleted_at | issues |
| `issue_mentions` | comment_id, member_id (PK), created_at | issues |
| `issue_subscriptions` | issue_id, member_id (PK), subscribed, reason, updated_at | issues |
| `issue_bookmarks` | issue_id, member_id (PK), created_at | issues |
| `external_issue_links` | id, issue_id (idx), kind, url, title, external_ref, created_by, created_at; unique (issue_id, url) | issues |
| `issue_merges` | id, primary_issue_id (idx), merged_issue_id (idx), merged_by, merged_at, unmerged_by, unmerged_at | issues |
| `ownership_rules` | id, position, source, pattern, team_id, enabled, created_by, created_at, updated_at | issues |
| `alert_rules` | id, name (unique), enabled, trigger_kind, threshold, window_minutes, filters_json, actions_json, action_interval_minutes, muted_until, last_fired_at, created_by, created_at, updated_at | alerts |
| `alert_fires` | id, rule_id, issue_id, trigger_kind, outcome, notification_count, run_id, error_code, fired_at; index (rule_id, issue_id, fired_at) | alerts |
| `notification_settings` | member_id, category (PK), channel, updated_at | notifications |
| `integration_checks` | kind (PK), checked_at, ok, error_code | integrations |

`filters_json` and `actions_json` are canonical JSON of typed dataclasses (architecture
section 6).

### 14. Data handling and leak matrix

New persisted data:

- enums, counts, ids and timestamps;
- redacted comment bodies;
- `Redactor`-checked admin and user text: view names and queries, rule names, ownership
  patterns, link titles and URLs;
- masked notification and GitHub bodies;
- audit details limited to ids, enums, counts and field names.

Raw values are never stored, logged, indexed, notified, rendered or returned. Error
responses never echo the query, URL, pattern or body. The Meilisearch documents do not
change.

| Threat | Control |
|--------|---------|
| Secret pasted into a search, view, pattern, link or title | `Redactor` reject (422, nothing stored, no echo); the logs pass the masking formatter |
| Secret in a comment or mention | Redacted before storage and mention parsing; DLP at notification |
| Tag, stats, dashboard or weekly report leaking values | Built only from columns that are already masked (detector, rule id, masked location head, enums, masked titles) |
| GitHub body leak | Allow-listed masked fields, `Redactor`, DLP gate, repo allow-list, dedicated least-privilege token |
| Archive as silent suppression | D3 bound, D4 exposure escalation, safety rules in section 7 |
| Merge hiding an exposure | Children keep their lifecycle; propagation reopens the primary; exception members blocked (D13) |
| Bulk bypassing checks | Per-issue service call, per-issue audit, permission checked before any change |
| Alert or mention spam | Action interval, mute, per-member settings, admin-only rule write |

The leak matrix is in `tests/leak/flows_v13.py` and `test_leak_matrix_v13.py`. It uses the
real `build_container`, the memory search backend, MockTransport Slack and GitHub, and a fake
SMTP server. Each flow feeds sample secrets and asserts all sinks: tables, logs, search
documents, outbound payloads, API bodies and tracebacks.

1. Saved views: 422 path and valid views.
2. Comment create and edit with a mention; the mention and subscription notifications; the
   `GET /api/issues/{id}/activity` response; the `issue_comments`, `issue_mentions`,
   `issue_subscriptions` and `issue_bookmarks` rows.
3. External link 422 path; GitHub create payload; DLP block.
4. Alert rule name 422; alert fired on a synthetic leak (fires, rows, payloads).
5. Events selector, tags, stats, dashboard and weekly report responses and payloads.
6. Bulk resolve and archive; merge and unmerge records and responses.
7. Archive, escalation and expiry history; search documents.
8. Integration status and test; ownership rule test 422 path; member edit; self token.
9. An invalid query that contains a sample secret: the 422 body and the logs.

### 15. Implementation plan shape

Plans follow `impl-plans/conventions.md` (including 7a). Each plan file stays under 400
lines and every Python file under 1000. Split points:

- `api/routers/issues.py` (851 lines): p01 splits it mechanically. `issues.py` keeps
  `IssuesRouterDeps` (with new defaulted fields `actors: ActorDirectory | None` and
  `suggester: AssigneeSuggester | None`, so no later plan edits it) and composes
  `issues_common.py` (serializers), `issues_list.py` (list, stats), `issues_detail.py` (get,
  events, history, occurrences), `issues_actions.py` (patch, remediation, resolve, release,
  exceptions) and `issues_comments.py`. Paths and behavior are unchanged.
- `issues/service.py` (978 lines): p05 first moves the post-insert transition block into
  `issues/ingest_transitions.py`.
- New routers get their own `<Area>RouterDeps` and are registered only by p12
  (`wiring_v13.py`).

No plan changes `pyproject.toml`, `uv.lock`, `src/web/package.json` or `bun.lock`.
`README.md` is owned by p17.

| Wave | Plan | Owns | Depends on |
|------|------|------|-----------|
| 1 | v13-p01-foundation | `src/telescreen/core/{vocabulary,permissions,config}.py`, `src/telescreen/ports/{issues,notifications,workflow,directory}.py`, `src/telescreen/issues/transitions.py`, the mechanical split only (`src/telescreen/api/routers/issues.py` composer and the newly created `issues_common.py`, `issues_list.py`, `issues_detail.py`, `issues_actions.py`, `issues_comments.py`, moved code with unchanged behavior and no new fields), `tests/core/v13/**`, `tests/ports/v13/**`, existing permission and vocabulary exact-count tests | - |
| 1 | v13-p02-db | `src/telescreen/db/models/**`, `src/telescreen/db/migrations/versions/0004_v1_3.py`, `tests/db/test_models.py`, `tests/db/v13/**` | - |
| 1 | v13-p03-ui-foundation | `src/web/src/api/**` (types, client, mock router and the realistic seed for every v1.3 endpoint), `src/web/src/components/**` (Avatar, PeriodPicker, StreamTabs, Sparkline use, palette search, shortcut registry and help overlay, nav rename), `src/web/src/{routes,App}.tsx`, `src/web/src/shell.test.tsx`, stub `index.tsx` for the new page directories, `src/web/scripts/screenshots.ts` (all new routes and states) | - |
| 2 | v13-p04-issue-stream-backend | `src/telescreen/issues/query/**`, `src/telescreen/issues/views.py`, `src/telescreen/api/routers/issues_list.py`, `src/telescreen/api/routers/issues_common.py` (single wave-2 owner of the shared item serializer; see the notes), any existing issue-API test that asserts the exact item key set (none found at `57f389b` in `tests/issues/test_issues_api.py` or `tests/api/v11_issues/**`; if one appears, p04 owns it), new `src/telescreen/api/routers/{issue_counts,issue_views}.py`, `tests/issues/v13_query/**`, `tests/api/v13_stream/**` (B2, B3, B5, S1, S2) | p01, p02 |
| 2 | v13-p05-triage-lifecycle-backend | `src/telescreen/issues/{service,ingest_transitions,lifecycle,repository,exceptions,policy_apply,archive,groups}.py`, `src/telescreen/api/routers/issues_actions.py`, new `src/telescreen/api/routers/issue_triage.py` (archive, unarchive, review), `tests/issues/v13_lifecycle/**` (S4, priority, review, group coordination, `ArchiveMaintenanceProvider`, findings observer and activity calls, clearing archive fields on exception approval) | p01, p02 |
| 3 | v13-p07-issue-detail-backend | `src/telescreen/issues/{activity,external}/**`, `src/telescreen/api/routers/{issues_detail,issues_comments}.py`, new `src/telescreen/api/routers/{issue_events,issue_activity,issue_external}.py`, `tests/issues/v13_detail/**`, `tests/api/v13_detail/**` (B1, B4, B6, B7, S5) | p01, p02, p04 |
| 2 | v13-p08-alerts-backend | new `src/telescreen/alerts/**`, `src/telescreen/workflow/triggers/**` (`RunStarter`), `src/telescreen/api/routers/workflows.py`, new `src/telescreen/api/routers/alerts.py`, `tests/alerts/**`, `tests/workflow/v13/**` (S6, workflows list) | p01, p02 |
| 2 | v13-p09-notifications-backend | `src/telescreen/notifications/**` (settings, `send_to_member`, category filter, tester, outbound guard, weekly report, built-ins), new `src/telescreen/api/routers/notification_settings.py`, `tests/notifications/v13/**` (S7) | p01, p02 |
| 3 | v13-p10-dashboard-integrations-backend | new `src/telescreen/issues/dashboard.py` (`IssueReportSource`), new `src/telescreen/integrations/**`, new `src/telescreen/api/routers/{dashboard,integrations}.py`, `tests/issues/v13_dashboard/**`, `tests/integrations/**` (S8, S9 integrations) | p01, p02, p04 |
| 2 | v13-p11-directory-ownership-backend | `src/telescreen/directory/**` (member edit, deactivate, `ActorDirectory`, self tokens), `src/telescreen/issues/{routing,ownership}.py`, `src/telescreen/api/routers/{directory,users}.py`, new `src/telescreen/api/routers/ownership.py`, `tests/directory/v13/**`, `tests/issues/v13_ownership/**` (S9) | p01, p02 |
| 2 | v13-p13-ui-stream | `src/web/src/pages/issues/{IssueList*,IssueBulkActions,query*,savedViews,issues.css,index}.ts(x)` and their tests | p03 |
| 3 | v13-p14-ui-detail | `src/web/src/pages/issues/detail/**`, `src/web/src/pages/issues/{IssueDetail*,issue-detail.css,uploadEvidence}.ts(x)` | p03, p13 |
| 2 | v13-p15-ui-alerts-profile | new `src/web/src/pages/{alerts,profile}/**` | p03 |
| 2 | v13-p16-ui-dashboard-org | new `src/web/src/pages/dashboard/**`, `src/web/src/pages/{settings,workflows}/**` | p03 |
| 3 | v13-p06-bulk-merge-backend | new `src/telescreen/issues/{bulk,merge}.py`, new `src/telescreen/api/routers/issue_bulk.py`, `tests/issues/v13_bulk/**` (S3); the run-1 carry-over UI side of the bulk contract (6.1): `src/web/src/api/client.ts` (`bulk` signature only), `src/web/src/api/mock/{router,mock.test}.ts` (bulk handler only), `src/web/src/pages/issues/IssueList{,.test}.tsx` | p03, p04, p05, p13 |
| 4 | v13-p12-wiring | `src/telescreen/wiring.py`, new `src/telescreen/wiring_v13.py`, `src/telescreen/api/app.py`, `tests/e2e/{test_api_routes,test_route_permissions}.py`, new `tests/e2e/{test_wiring_v13,test_route_permissions_v13}.py` | p04-p11 |
| 5 | v13-p17-verification-docs | `tests/leak/**` (new v13 files), new `tests/e2e/test_flow_v13_*.py`, new `tests/live/v13/**`, `src/web/scripts/screenshots.ts` (strict-run fixes only), `README.md`, `impl-plans/README.md`, status lines of the design documents. Archiving is the serial finalization, not p17 | all |

Plan-to-file notes:

- Shared item serializer: p01 creates `issues_common.py` by moving `_issue_json` and
  `_issue_detail_json` out of `issues.py` unchanged, and hands it to p04 for wave 2 (an
  explicit handoff). p04 is its only wave-2 editor. p04 extends the serializer in place,
  keeping the function names, with:
  - `priority`, `priority_manual`, `needs_review`;
  - `assignee {kind, ref, display_name}`, through `IssuesRouterDeps.actors`; without
    `actors` the display name falls back to the ref;
  - `archive`, `merged_count`, `bookmarked`, `updated_at`.

  `trend` stays list-only in `issues_list.py`.
- p05 and p07 do not edit `issues_common.py`:
  - p05's action, PATCH and triage routes keep calling the shared serializer and get the
    new fields after integration;
  - p07 adds the detail-only fields (`subscription`, `merged_into`, `merged_children`,
    `suggested_assignees`) in `issues_detail.py`, on top of the shared serializer output.

  p05 and p07 tests assert only their own fields (and the presence of `id`), never the full
  item key set, so they keep passing when p04's fields arrive. p04's tests own the
  key-set assertions.
- p05 owns `issues/repository.py` and the `RECONCILED_STATUSES` change. p04 reads issue
  tables through its own query module.
- p11 changes `IssueOwnerRouter` and implements `AssigneeSuggester`. p05 does not edit
  `routing.py`.
- p03 creates stub `index.tsx` files for every new route, including new files under
  `pages/settings/`. p15 and p16 replace them in wave 2 (explicit handoff, as with the v1
  shell stubs).
- p07 implements `IssueActivityNotifier`; p05 and p07 call it through the port.
- A plan that breaks a shared exact-count or inventory test owns that test. Known owners:
  `tests/db/test_models.py` (p02); the route tests (p12); the page `*.test.tsx` files (the
  page plan); `src/web/src/api/mock/*.test.ts` and the screenshot list (p03); new
  `tests/leak/**` v13 files (p17).
- Shared-test ownership per dispatch manifest (added 2026-10-12 after the run-1 blockers,
  which were all tests outside the breaking plan's writePaths). Before a manifest is
  written, each plan is checked against the existing tests its change can break, and every
  such file is listed by exact path in that plan's manifest `writePaths`. Plans in the same
  wave split these files one owner per file; a plan that does not own a file must leave it
  passing unchanged, and a failure there is a reconciliation request, not an edit. A plan
  in a later wave may own a file that an earlier wave owned (for example
  `src/web/scripts/screenshots.ts`: p03 in wave 1, p13 in wave 2 for stream selectors).
  The route walk (`tests/e2e/test_route_permissions.py`, `tests/e2e/test_api_routes.py`)
  and `tests/api/v12_rbac/test_v12_rbac_router_permissions.py` see only routers built by
  `build_container`, so new unregistered router modules (wave 2) do not change them; they
  are owned by p12 unless a wave-2 plan adds a route to an already registered module.

Runs (at most 8 plans each; later runs record accepted plans as `acceptedDependencies`).
The plan waves above are authoritative; a run dispatches whole waves or wave slices in
order:

| Run | Plans |
|-----|-------|
| 1 | p01, p02, p03 (wave 1); p04, p05, p09, p13 (wave 2) |
| 2 | p08, p11, p15, p16 (wave 2); p06, p07, p10, p14 (wave 3) |
| 3 | p07 (wave 3, re-verify), p12 (wave 4), p17 (wave 5), then serial finalization |

Run 1 was dispatched twice: manifest `telescreen-v13-20261012-e1` (blocked on write paths;
the owner reconciled at `d8073b1`) and `telescreen-v13-20261012-e2` (same scope; p01 and
p02 in re-verify mode, p03 finishing its remaining deliverables).

Run 2 (session 13, base `9accc07`): p01-p05, p09 and p13 are integration-accepted
dependencies. p12 and p17 stay for run 3, and no plan is archived. Shared files, checked
against the plans and the tree at `9accc07`:

- **Carry-over items, all owned by p06** (wave 3, section 6.1), each with a regression
  test that fails on the old behavior:
  - INT-W2-04: all-matching bulk in a `custom` period sends and honors `start`/`end`
    (server body, `client.ts` `bulk`, `IssueList.tsx`, mock bulk handler). The server
    alone is not enough, because `period=custom` without `start`/`end` is a
    `VALIDATION_ERROR`.
  - INT-W2-05: "Mark remediated" with all matching sends bulk `request_verification`
    instead of the "finite selected set" warning.
  - archive `until_escalating` parsing: bulk `params.archive` goes through
    `validate_archive_request`, with no default kind. The mock bulk handler stops
    defaulting `kind` to `for_duration` and stops giving every kind a 24 h `until`: the
    count and escalation kinds get `archived_at + 90 days`.
- **Inventory tests are read-only in run 2.** No run-2 plan adds a table, migration,
  template variable or notification event kind. So `tests/db/test_models.py`,
  `tests/notifications/**` counts and `tests/leak/**` stay unchanged and must keep
  passing. Each run-2 backend plan asserts the masking of its own new surfaces in its own
  test directory. The full v13 leak matrix stays with p17.
- **Route walk.** The new router modules (`alerts`, `ownership`, `issue_events`,
  `issue_activity`, `issue_external`, `issue_bulk`, `dashboard`, `integrations`) stay
  unregistered until p12, and their tests build the router directly. A composed router
  copies `APIRoute` objects as `api/routers/issues.py` does, never `include_router`
  (FastAPI 0.142 keeps an included router as a lazy `_IncludedRouter`, which the walk
  cannot see). The only run-2 plan that adds routes to an already registered module is
  p11 (`directory.py`, `users.py`). p11 therefore owns
  `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`, an exact per-module inventory.
  `tests/e2e/test_route_permissions.py` and `tests/e2e/test_api_routes.py` are read-only:
  they check that each registered route has exactly one marker, not a route count. p08
  changes only the `workflows.py` list payload, and p07 adds no route to
  `issues_detail.py` or `issues_comments.py`.
- **Frontend.** p15, p16 and p14 do not edit `src/web/src/api/**` or `components/**`.
  `screenshots.ts` already lists every run-2 state (p17 runs it strict in run 3). The
  pages render the accessible names that those states target:
  - tab "History" on `/alerts/alert-01`;
  - button "Edit Alex Morgan" on `/settings/users`;
  - button "Getting started" on `/dashboard`;
  - button "More" on the issue detail.

  The member-edit state at `9accc07` targets the exact name "Edit Alex". The mock member
  is "Alex Morgan", and the Edit button is named "Edit <display_name>" so that it is
  unique per row. p16 is therefore the single run-2 editor of `screenshots.ts` (wave 2),
  and it changes only that one action name. Every other plan keeps the file read-only.

  In wave 3, p06 is the only editor of `client.ts`, the mock router and its test, and
  `IssueList{,.test}.tsx`. `types.ts` and the mock fixtures are read-only, because
  `BulkParams` already carries `archive` and `verification`.
- **Same-wave rule.** If a manifest audit finds a file that two same-wave plans must edit,
  one plan owns it and the other declares `dependsOn` on the owner and runs after it
  inside the wave. This audit found no such file.

Run 3 (session 14, base `ca818c3`, manifest `telescreen-v13-20261013-e4`): p01-p06, p08-p11
and p13-p16 are accepted dependencies and are not redispatched. Each wave holds one plan,
and the waves run serially: A (p07) -> B (p12) -> C (p17) -> finalization. So no two plans
ever share a file, and each plan owns every shared file its change can break:

- **Wave A, p07 re-verify.** The code is complete at `ca818c3`. The plan reruns its
  verification on the committed tree and edits only its own plan file; a failing check is
  fixed inside the p07 writePaths. Its result follows the owner note in its Progress Log
  and conventions 7a: INT-E3-P07-01 (fixed by p10) appears only in `addressedFeedback`, and
  `findings`, `risks`, `priorVerification` and the self-check findings and residual risks
  are empty.
- **Wave B, p12.** It owns `wiring.py`, `wiring_v13.py`, `api/app.py`, the route walk
  (`tests/e2e/test_route_permissions.py`, `tests/e2e/test_api_routes.py`), the new
  `tests/e2e/test_wiring_v13.py` and `test_route_permissions_v13.py`. Every v1.3 router
  listed in section 15 is registered through `container.routers`, before the issues
  router; composition copies `APIRoute` objects, never `include_router` (FastAPI 0.142).
  The `build_container` signature is unchanged, so every existing caller (e2e harness,
  `tests/api/v12_rbac/rbac_world.py`) keeps working. Read-only and expected to pass
  unchanged: `tests/api/v12_rbac/**` (its inventory filters to a fixed `MODULES` tuple, so the
  new v1.3 modules do not enter it), `tests/db/test_models.py`, `tests/leak/**`,
  `tests/notifications/**` and `tests/examples/**`, since p12 adds no table, flow,
  template or example.
- **Wave C, p17.** It owns the new `tests/leak/{flows_v13,test_leak_matrix_v13}.py`, the
  `tests/e2e/test_flow_v13_*.py` flows, `tests/live/v13/**`, `src/web/scripts/screenshots.ts`
  (it is the only run-3 screenshot runner; edit only when the strict run fails because a
  state targets a name that differs from the accepted page, and record the reason),
  `README.md`, `impl-plans/README.md` and the v1.3 status lines of this document,
  `design-v1-3-sentry-parity-review.md`, `design-ui-sentry.md` (11, 11.4),
  `design-api-and-ui.md` ("v1.3 endpoints"), `design-data-model.md` ("v1.3 additions") and
  `architecture.md` (section 4 "v1.3 additions"). Existing leak flows, `tests/leak/harness.py`,
  `sinks.py`, `tests/db/test_models.py`, `tests/examples/**`, `src/web/src/**` and feature
  code stay read-only; a defect there is a reconciliation request naming the owning plan.
- **Finalization (serial, after p17 is integration-accepted).** On `main`:
  - move `impl-plans/active/v13-p*.md` to `impl-plans/completed/`, and the e1, e2, e3 and
    e4 manifests to `impl-plans/completed/dispatch/` (the v1-v1.2 convention);
  - move the v1.3 rows of `impl-plans/README.md` to its completed section;
  - rerun `mise run ci` and the UI gates of section 16 on the final tree;
  - only when every gate passes, commit and push to `origin/main`.

### 16. Verification

- **Python**: `uv run ruff format --check . && uv run ruff check . && uv run python
  tools/typelint.py && uv run ty check && uv run pytest && uv build`.
- **UI**: `cd src/web && bun install --frozen-lockfile && bun run typecheck && bun run test
  && bun run build && bun run screenshots`. Every new page and state is captured in light
  and dark with zero browser errors: the stream tabs, period, bulk selection, archive menu,
  detail (header menus, activity, tags), alerts list, editor and history, dashboard,
  profile, integrations, tokens, ownership rules, users with member edit, workflows list,
  palette open, shortcut overlay, and onboarding.
- **E2E flows (p17)**:
  - **E1** stream: counts, negation, `has:`, dates, shared and default views, bulk resolve
    by query, merge and unmerge;
  - **E2** archive:
    - archive until 5 findings, then ingest, `escalating`, an `issue_escalating` alert once
      per interval, and duration expiry with `FakeClock`;
    - merge into an archived primary: archive primary P (`until_findings` 5) and merge a
      child that has 40 historical findings. P stays `archived` and `archive_until` is
      unchanged. Four new findings leave P archived; the fifth moves it to `escalating`;
    - unmerge: the archived child escalates only after 5 findings of its own;
  - **E3** detail: navigation over 120 findings, tags, a mention that notifies by email,
    subscriber activity;
  - **E4** organization: an ownership rule routes a new issue; deactivation releases
    issues; self token;
  - **E5** weekly report through a scheduler tick.
- **Live (`tests/live/v13`, marker `live`)**:
  - an alert rule fires on a synthetic leak and notifies Slack
    (`TELESCREEN_TEST_SLACK_CHANNEL`) and Mailpit exactly once within the action interval;
  - a mention notifies the mentioned user by email according to their settings.
  - The dev-stack check of review section 6 (Keycloak, Mailpit, Slack test channel) is
    `uv run --frozen pytest -m live tests/live -p no:cacheprovider`. It runs the existing
    Keycloak sign-in and role-mapping test (`tests/live/test_keycloak_oidc.py`,
    `TELESCREEN_TEST_KEYCLOAK_URL`), the existing v1.2 Mailpit and Slack delivery tests and
    the two v1.3 tests above. Live tests skip
    when their variables are unset; a skipped live test is reported as "not run", never as
    passed, and it is not a pass gate for the offline CI.

### 17. Author self-check against the request

| Request item | Section |
|--------------|---------|
| Verify and correct the review | review section 7 |
| B1-B7 with regression tests | 3 |
| S1-S10 | 4-13 |
| Deferrals kept (releases, environments as sources, projects as sources and teams, Discover, Jira create, replay/performance/profiling) | not designed; external issues allow Jira by URL only |
| Masking contract and leak matrix | 14 |
| Archive never permanent; exceptions unchanged | D2, D3, D13, 7 |
| Every route declares a permission | 2, 15 (p12 route walk) |
| Plans, file sizes, waves, runs | 15 |

## References

See `design-docs/references/README.md`. External: Sentry product documentation for issue
states (unresolved substatuses, For Review, archive conditions, escalating), the issue
stream search syntax, alert rules (conditions, filters, action interval) and personal
notification settings; GitHub REST "Create an issue".
