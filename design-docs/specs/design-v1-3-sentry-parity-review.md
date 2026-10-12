# Telescreen v1.3: Sentry Parity Self-Review

Status: review accepted by the owner request of 2026-10-12; input to the v1.3 design and plans.

The owner asked (translated): "Self-review the UI and the features. Do we provide the same
usability and functionality as Sentry? If not, improve it." This document records the
review of `main` at `583751c`, the gaps it found and the scope adopted for v1.3. The v1.3
design (data model, API, UI, plans) builds on it and on `design-ui-sentry.md`.

## 1. Method

- Code inventory of every UI route and API router against a 60-item Sentry capability
  checklist (issue stream, issue detail, alerts, notifications, organization, dashboards,
  other UX). Result: 9 present, 23 partial, 28 missing.
- Visual review of the screenshot smoke (`tmp/ui-screenshots/*`, light and dark) and of
  the live UI signed in through Keycloak.
- Domain filter: Telescreen issues are leaked secrets and personal data, not crashes.
  Sentry features are adopted when they help triage and response of exposures; features
  tied to software releases are mapped or deferred (section 5).

## 2. Defects found (fix first)

| Id | Defect | Evidence |
|----|--------|----------|
| B1 | Finding navigation is reversed: "First" opens the newest and "Latest" the oldest, because the API returns events newest first | `src/web/src/pages/issues/detail/IssueDetailSections.tsx:166-195`, `api/routers/issues.py:289` |
| B2 | `assigned:none` raises `RECIPIENT_UNKNOWN` instead of listing unassigned issues | `issues/search` -> `directory/resolver.py:97` |
| B3 | The default `is:unresolved` view hides regressed issues; Sentry's Unresolved includes them | `src/web/src/pages/issues/query.ts`, `api/routers/issues.py:117-182` |
| B4 | Assignee is shown as a raw member or team id (`mem-01`) in the list and the detail select | `IssueListRows.tsx:146-150`, `IssueDetailHeader.tsx` |
| B5 | Query negation (`!key:value`) is silently dropped | `query.ts:99` |
| B6 | Sidebar Activity shows only session-local comments; the timeline has no actor | `IssueDetailSidebar.tsx:141-158` |
| B7 | Event navigation, occurrence chart and tag chips use only the first 50 loaded events | `IssueDetailSections.tsx:32-63,120-147` |

## 3. Gap summary

| Area | Sentry behaviour | Telescreen today | Gap |
|------|------------------|------------------|-----|
| Stream tabs | Unresolved, For Review, Regressed, Escalating, Archived, Resolved with counts | Left-nav Feed, For review, Allowed, Verifying | No status tabs or counts; no Regressed/Escalating/Resolved views |
| Trend | Per-issue graph column, 24h/14d toggle | One global 14-day bar | No per-issue trend (`Sparkline.tsx` is unused) |
| Period | Time range picker scopes counts and list | None | Missing |
| Columns | Events, Users | Findings, Risk, Priority | No affected-resources column (`distinct_fingerprints` already returned) |
| Search | Rich syntax, negation, `has:`, relative dates, numeric comparisons, saved searches on server | 6 keys, LIKE free text, localStorage views | Partial |
| Bulk | Resolve, archive, assign, merge, select all matching | Client loop; no resolve or ignore | Partial |
| Refresh | Live stream | Manual | Missing |
| Detail header | Resolve split, Archive split (until N events, for duration, forever, until escalating), Assign, Priority, Bookmark, Subscribe, Share | Many flat buttons; ignore only through an approved exception; priority read-only | Partial |
| Event view | Oldest/prev/next/latest/recommended, tags, contexts | Exists, but B1 and B7 | Partial |
| Tags | Server-aggregated top values with percentages | Top 4 from loaded events | Partial |
| Activity | Comments with actor, edit/delete, @mentions that notify | Comments stored, no actor, no mentions | Partial |
| Merge | Merge and unmerge issues | Linked occurrences only | Missing |
| External issues | Link or create GitHub/Jira issues | None | Missing |
| Ownership | Ownership rules UI, suggested assignees | CODEOWNERS and owner routing in backend only | Partial |
| Alerts | Alert rules with conditions (new, regression, frequency over interval, affected count), filters, actions, action interval, mute, history | Workflow triggers on created/regressed with 3 filters, YAML only | Partial |
| Notifications | Per-user settings (email/Slack per category), issue subscriptions, weekly report | Members have email/Slack fields only; owner digest | Missing |
| Dashboard/stats | Overview and stats pages | None | Missing |
| Integrations | Page with install status | Collectors page; Slack token from env | Missing |
| Tokens | API token UI | Backend only | Missing |
| Palette/shortcuts | Searchable palette; j/k, action shortcuts, `?` help | Palette lists areas only; no list shortcuts | Partial |
| Onboarding/docs | Getting-started checklist, docs links | Empty-state link only | Missing |
| Workflows list | (Sentry alert list analog) name, conditions, last triggered, status | Name and versions only | Partial |
| Mock data | Realistic volume | One unresolved issue in the stream | Reviews and screenshots cannot show a realistic stream |

## 4. Adopted scope for v1.3

All items keep the masking contract: no raw value is stored, indexed, logged, rendered,
notified or returned; new aggregates, tags, search keys, alert payloads, activity and
mentions carry masked values or fingerprints only, and the leak matrix covers them.

- **S0 Defects**: fix B1-B7 with regression tests.
- **S1 Issue stream**: status tabs with counts (Unresolved = unresolved + regressed +
  escalating, For review, Regressed, Escalating, Archived, Resolved); per-issue trend column
  from server-side buckets with a 24h/14d toggle; period picker (24h, 7d, 14d, 30d, custom)
  that scopes counts, trend and list; Resources column; sort by priority and trending;
  member and team display names with initials avatars everywhere; auto refresh with a
  "new or updated issues" banner instead of reordering under the user.
- **S2 Search and views**: negation, `has:`, `first_seen:`/`last_seen:` with relative and
  absolute dates, `times_seen:`/`resources:` comparisons, `priority:`, `assigned:none`,
  `assigned:#team`, `is:archived|escalating|regressed|for_review`, autocomplete for keys and
  values, and server-side saved views (personal and shared, starred, default view per user).
- **S3 Bulk actions**: server-side bulk endpoint for resolve, archive, assign, priority,
  status changes and merge, with "select all N matching" over the current query; each issue
  change is audited individually and permission-checked.
- **S4 Archive and escalating**: Sentry-style Archive (snooze) without approval: until N
  more findings, until N more affected resources, for a duration (max 90 days), or until
  escalating. Archived issues return to the stream as Escalating when findings exceed a
  forecast/threshold, or as Regressed when a resolved issue recurs. Approved exceptions
  ("Allowed") stay the only permanent suppression and keep their approval flow.
- **S5 Issue detail**: Sentry-like header (Resolve split, Archive split, assignee picker with
  suggested owners, editable priority, bookmark, subscribe, copy permalink, "More" for the
  rest); event navigation served by the API (oldest, previous, next, latest, recommended,
  with paging beyond 50); server-aggregated tag distribution with percentages; per-issue
  stats endpoint and chart with the period picker; merged activity feed with actors,
  comments (edit/delete own), and @mentions of members that notify them; merge and unmerge;
  linked external issues (link by URL for any tracker; create a GitHub issue when a GitHub
  integration token is configured, body masked).
- **S6 Alerts**: alert rules as first-class objects with a form builder: triggers (new
  issue, regression, escalating, frequency of findings over an interval, affected resources
  over an interval), filters (severity, priority, source, rule class, exposure, tag, owner
  team), actions (notify a notification set or recipients, start a workflow), action
  interval (at most once per N minutes per issue), enable/disable, mute for a period, and
  alert history (fired, suppressed by interval, delivered). Workflow triggers stay; rules can
  start workflows.
- **S7 Notifications**: per-user notification settings (categories: assigned to me,
  mentions, subscribed issue activity, alerts, workflow tasks, weekly report; channel per
  category: email, Slack DM, off), issue subscriptions (auto-subscribe on assign, comment,
  mention), and a weekly report digest (counts, new, resolved, top issues, MTTR) through the
  editable templates.
- **S8 Dashboard and stats**: overview page with period picker: findings over time by
  severity, new vs resolved, open by exposure and source, top issues, MTTR and
  time-to-acknowledge, unassigned high-priority count, with links into the stream.
- **S9 Organization**: integrations page (Slack, email/SMTP, GitHub, Google, OIDC: configured,
  last check, setup link, test action where one exists), API token UI (create, list,
  revoke, scopes, member binding; secret shown once), member edit/deactivate, ownership
  rules UI (source/location pattern -> team, ordered, tested against a sample locator)
  feeding owner routing and suggested assignees.
- **S10 UX polish**: searchable command palette (pages, issues by short id or title,
  actions), keyboard shortcuts (j/k, o/Enter, x select, r resolve, a assign, e archive, `?`
  help overlay), getting-started checklist on a new instance (connect a collector, configure
  notifications, invite users, create an alert rule), docs links, a user profile page
  (notification settings, my tokens, theme), a richer workflows list (trigger, enabled,
  last run, run counts), and a realistic mock seed (at least 40 issues across every status,
  source, severity and exposure, several assignees, comments, archived and escalating
  issues) so the screenshot smoke shows real stream behaviour.

## 5. Deferred or mapped

| Sentry feature | Decision | Reason |
|----------------|----------|--------|
| Releases, "resolve in next release" | Deferred | No release concept for leaked data; verified resolution (v1.1) covers "is it really gone" |
| Environments | Mapped to sources | Source (Slack, GitHub, Drive, ...) is the scoping axis |
| Projects | Mapped to sources and owner teams | Avoids a new tenancy layer |
| Discover (ad-hoc aggregation) | Deferred | Dashboard and search cover v1.3 needs |
| Jira create | Deferred | Link-by-URL covers it; GitHub create is in scope |
| Session replay, performance, profiling | Out of scope | Not applicable |

## 6. Acceptance

- Every S0-S10 item implemented as designed, with unit, API, UI and E2E tests; every new
  API route declares a permission and the route-walk test passes.
- Leak matrix extended to tag aggregates, stats, saved views, bulk and merge records, alert
  rules and history, subscriptions, mentions, activity, external issue bodies, weekly report
  and integration status: no raw sample value appears anywhere.
- Screenshot smoke covers every new page and state in light and dark with zero browser
  errors; the stream screenshots show the realistic mock seed.
- Live check on the dev stack (Keycloak, Mailpit, Slack test channel): an alert rule fires on
  a synthetic leak and notifies Slack and email once per action interval; a mention notifies
  the mentioned user according to their settings.
