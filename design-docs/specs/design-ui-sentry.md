# UI Design: Sentry-Based Reporting Bureau UI

The Telescreen UI (`src/web`, SolidJS) follows the structure, information hierarchy and
interaction patterns of the current Sentry web UI (2025-2026 navigation and streamlined issue
details, "chonky" S.C.R.A.P.S. design system). Owner decision (2026-10-09): Sentry is the
design base for every page.

## Overview

Research was done against `getsentry/sentry` master (HEAD `3fd2bd7`, 2026-10-08), Sentry
docs, changelog and blog. Primary sources are listed in "Sources" at the end.

Licensing constraint (verified): Sentry's repository and its design-system package are
`FSL-1.1-Apache-2.0`; the design-system README says it "is not intended for use in other
products"; trademarks may not be used. Therefore Telescreen:

- re-implements patterns (layout, hierarchy, interaction models, token *concepts*) in its own
  SolidJS code;
- never copies Sentry source (TSX, Emotion styles, generated token files), icons,
  illustrations, logos, product names (Sentry, Seer, Scraps) or Figma assets;
- uses its own color palette (Sentry's brand "blurple" `#7553FF`, pink and lime set is
  identity-bearing and is not used);
- uses Rubik and Roboto Mono (SIL OFL, Google Fonts) and the Lucide icon set (ISC license,
  `lucide-solid`), which are independent of Sentry.

## Details

### 1. Design tokens (`src/web/src/styles/tokens.css`)

CSS custom properties on `:root`; dark theme on `[data-theme="dark"]`; `prefers-color-scheme`
selects the default ("System"); Settings offers System / Light / Dark (persisted in
`localStorage`, never on the server).

Color roles (Telescreen palette; roles mirror Sentry's semantic token structure):

| Token | Light | Dark | Use |
|-------|-------|------|-----|
| `--bg-primary` / `--bg-secondary` / `--bg-tertiary` / `--bg-overlay` | `#FFFFFF` / `#F7F8FA` / `#EEF0F4` / `#FFFFFF` | `#262A33` / `#1F232B` / `#181B22` / `#30353F` | Higher elevation = lighter in both themes |
| `--border-primary` / `--border-secondary` | `#D9DCE3` / `#E6E8EE` | `#12151B` / `#181B22` | Panels, rows, separators |
| `--text-primary` / `--text-secondary` / `--text-heading` / `--text-disabled` | `#2A2F38` / `#646C7A` / `#151A22` / `#8A91A0` | `#E4E7EC` / `#AEB4BF` / `#FFFFFF` / `#8F96A3` | |
| `--accent` / `--accent-strong` / `--accent-content` / `--accent-muted-bg` / `--accent-muted-border` | `#2F6BDB` / `#2257BB` / `#1F55B8` / `#2F6BDB14` / `#C9D8F6` | `#4A84F0` / `#2F6BDB` / `#9DBDF7` / `#2F6BDB33` / `#2D4E8F` | Primary actions, selection, links, focus ring |
| `--danger` / `--danger-strong` / `--danger-content` / `--danger-muted-bg` | `#E5484D` / `#C4282D` / `#C4282D` / `#E5484D14` | `#E5484D` / `#5E1A1D` / `#F2A1A3` / `#E5484D29` | Exposed secrets, destructive actions |
| `--warning` / `--warning-strong` / `--warning-content` / `--warning-muted-bg` | `#F5B83D` / `#C98A12` / `#9A5B00` / `#F5B83D24` | `#F5B83D` / `#3D2A05` / `#F5C870` / `#F5B83D29` | Medium severity, SLA nearing breach |
| `--success` / `--success-strong` / `--success-content` / `--success-muted-bg` | `#30A46C` / `#1E7A4E` / `#1E7A4E` / `#30A46C1A` | `#30A46C` / `#0F3D27` / `#7FD1A6` / `#30A46C29` | Resolved, verified by Police |
| `--hover-bg` / `--active-bg` / `--selected-bg` | `#1A243608` / `#1A24360F` / `#2F6BDB0F` | `#FFFFFF0A` / `#FFFFFF14` / `#4A84F01F` | Transparent interactive overlays |
| `--chart-total` / `--chart-matching` / `--chart-release` | `#D9DCE3` / `#2F6BDB` / `#8A91A0` | `#3A404B` / `#4A84F0` / `#8F96A3` | Bar charts and sparklines |

Other tokens (values follow Sentry's scale; scales are generic, not identity-bearing):

- Typography: `--font-sans: Rubik, "Avenir Next", "Hiragino Sans", "Noto Sans JP",
  system-ui, sans-serif` (Japanese text falls back to system fonts); `--font-mono:
  "Roboto Mono", Monaco, Consolas, monospace`. Sizes `xs 11`, `sm 12`, `md 14` (body), `lg 16`,
  `xl 20`, `2xl 24`, `3xl 32` px. Weights 400 / 500. Line heights 1, 1.2, 1.4.
  Numbers in tables use `font-variant-numeric: tabular-nums`.
- Spacing `2xs 2`, `xs 4`, `sm 6`, `md 8`, `lg 12`, `xl 16`, `2xl 24`, `3xl 32` px.
- Radius `xs 4`, `sm 5`, `md 6`, `lg 8`, `xl 12`, `full 999` px.
- Shadows are solid "ledge" shadows (no blur): low `0 1px 0 0 #1A24360F`, medium
  `0 2px 0 1px #1A243608, 0 1px 0 0 #1A243608`, high `0 4px 0 2px #1A243608, 0 1px 0 1px
  #1A243608`. Floating elements (tooltips, menus, drawers, modals, toasts) use shadows;
  in-page surfaces use borders.
- Motion: durations 120 / 160 / 240 ms; easing `snap cubic-bezier(0.8,-0.4,0.5,1)`, `smooth
  cubic-bezier(0.72,0,0.16,1)`, `enter cubic-bezier(0.24,1,0.32,1)`; all motion disabled under
  `prefers-reduced-motion`.
- Z-index: header 1000, dropdown 1001, sticky 1002, sidebar 1020, drawer 9999, modal 10000,
  toast 10001, tooltip 10003.
- Viewport breakpoint `md 992px` (mobile navigation below). Container sizes for container
  queries: `sm 384`, `md 448`, `lg 640`, `xl 768`, `2xl 896`, `3xl 1024`, `4xl 1152` px.

### 2. Core components (`src/web/src/components/ui/`)

| Component | Pattern | Spec |
|-----------|---------|------|
| `Button` | Sentry "chonky" embossed button | Two layers: a `::before` edge in the variant's strong color and an `::after` surface with a 1px border. The surface and label lift by `--lift` (2px md/sm, 1px xs, +1px on hover, 0 on `:active`, `[aria-expanded=true]`, disabled or busy). Transition `transform 120ms snap`. Variants `default` (surface `--bg-primary`, edge `--border-primary`), `primary` (accent), `danger`, `transparent` and `link` (no lift; hover `--hover-bg`). Sizes md 36 / sm 32 / xs 28 / zero 24 px height; radius 8 / 6 / 5 / 4; font 14 / 14 / 12 / 12. Focus-visible: 2px `--accent` ring. Disabled opacity 0.6 |
| `SplitButton` | Resolve / Ignore | Main action plus a chevron opening a `Menu` |
| `Tag` | Status and priority tags | Height 20px, 12px text, radius 4, padding `0 8px`. Variants: muted (`--text-secondary` on `--bg-tertiary`), info (`--accent-content` on `--accent-muted-bg`), danger, warning, success |
| `StatusIndicator` | Health dot | 8px dot with a pulsing muted halo (no pulse under reduced motion). `aria-label` when meaningful (`role="img"`), otherwise `aria-hidden` |
| `SeverityBar` | Error-level bar | 3px x 1em bar before the message: high `--danger`, medium `--warning`. Accompanied by visually hidden text |
| `PriorityBadge` | Cell-signal priority | Muted tag with a 3/2/1-bar signal icon (high/medium/low); not color-coded. Dropdown "Set priority" |
| `ProgressMarker` | Remediation progress pie | Five steps: Identified -> Assigned -> Contained -> Fix proposed -> Remediated (empty, 1/4, 1/2, 3/4, full) |
| `SearchQueryBuilder` | Structured search tokens | Free text plus `key:value` pills (24px high, radius 5, 1px muted border). Each pill is a grid of key / operator / value / remove, each focusable (`aria-label` "Edit value for filter: X", "Remove filter: X"). Focused pill: `--bg-tertiary` with `--accent` border. Invalid key: danger border on danger-muted background, tooltip naming the supported keys. Autocomplete combobox for keys and values. Supported syntax: `!` negation, `*` wildcard, `key:[a, b]`, `> < >= <=` on counts and dates. Serializes to the API's query parameters |
| `PageFilterBar` | Project / env / date filter group | Joined segmented control: Source (slack, gdrive, gdocs, gsheets, github, custom, police), Collector config, Date range (1h, 24h, 7d, 14d default, 30d, 90d, absolute). Persisted per user in `localStorage` |
| `SortSelect` | Sort menu with descriptions | Options with a detail line (see Issues) |
| `Panel`, `PanelHeader`, `PanelItem` | Bordered panel and rows | Radius 6, `--border-primary`, header `--bg-secondary`, 12px uppercase-free headings |
| `Table` | Simple table | Header 12px `--text-secondary` medium; rows 14px; row hover `--hover-bg` |
| `Pagination` | Cursor pagination | Previous / Next buttons only (`sm`), from the API `next_cursor` |
| `Drawer` | Global singleton right drawer | Resizable width persisted in `localStorage`; `blocking` (scroll lock, closes on outside click or route change) and `passive` modes; Escape closes; focus trap |
| `FoldSection` | Collapsible detail section | Header button "Collapse X section" / "View X section"; state persisted per key in `localStorage`; deep-linkable by URL hash |
| `Timeline` | Breadcrumb timeline | Grid `22px | minmax(50px, 1fr) | auto`: circular icon badge, bold 14px title with 12px secondary content, right-aligned timestamp; vertical connector line; `Timeline.Data` blocks in mono 12px |
| `Redacted` | Masked value rendering | Masked spans render on a faint danger background (`--danger-muted-bg`) in mono; tooltip states the masking method (Masked, Removed, Replaced, Fingerprinted) and the rule that caused it (rule id and class). Never renders raw values (there are none in the API) |
| `EmptyState` | Empty states | Title, description, optional action; plain wording ("No issues match your search" with tips to check filters and syntax) |
| `Placeholder` | Loading skeleton | `--bg-tertiary` blocks, radius 6; row skeletons mirror each column |
| `MiniBarChart` | Trend sparkline | 36px high; total bars `--chart-total`, matching bars `--chart-matching`; dashed max line with an abbreviated label; inline SVG, no chart library |
| `RelativeTime` | Time display | "3h ago" with absolute timestamp tooltip |
| `Toast` | Notifications | Bottom-left, auto-dismiss, `role="status"` |
| `Tooltip`, `Menu`, `Select`, `Hovercard` | Floating elements | Ledge shadows, `--bg-overlay`, z-index tokens |

Icons: `lucide-solid` (ISC). Fonts: self-hosted Rubik and Roboto Mono woff2 files under
`src/web/public/fonts/` with their OFL license files (no runtime Google Fonts request).

### 3. Application shell

- **Primary rail** (74px, `--bg-primary`, right border, `aria-label="Primary navigation"`):
  organization label at the top; items Issues, Search, Workflows, Police, Collectors,
  Escalation, Audit, Assistant, Settings (Runs has no rail item; it is the second link of
  the Workflows secondary panel and its routes highlight the Workflows item); each item is a 32px square icon container (radius
  6) with an 11px label below. Hover: `--hover-bg` with a muted border. Active
  (`aria-current="location"`): `--selected-bg` with `--accent-muted-border`.
- **Secondary panel** (default 190px, resizable 150-450px, persisted; `--bg-secondary`):
  title plus collapse button; `mod+b` toggles collapse; when collapsed, hovering the rail
  "peeks" the panel as an overlay. Sections are collapsible and separated by muted
  separators. Links are 32px high with optional leading icon and trailing count badge.
  Hovering a rail item switches the panel to that group.
- **Top bar** (sticky, 53px, `--bg-secondary`, bottom border): breadcrumb slot that also
  carries the single page `<h1>`, page actions slot, a global "Ask Assistant" button and a
  search button. Pages fill slots through a `TopBar.Slot` component.
- **Mobile** (< 992px viewport): rail and panel collapse into a top bar with a menu button
  and a slide-in panel.
- **Accessibility**: "Skip to main content" link targeting `#main`; single `<h1>`; landmarks;
  visible focus ring; keyboard operable menus; color never the only signal (severity and
  status carry text).
- **Shortcuts**: `mod+k` command palette (navigate, run issue actions, switch views),
  `mod+b` collapse navigation, `mod+/` open Assistant, `Esc` close drawer or palette.

Sections 4-6 describe the full target pattern. Section 8 is binding for v1: it limits
every page to what the accepted API (commit 28ce81b) returns and lists the parts that are
omitted until a follow-up API exists.

### 4. Issues list (`/issues`)

- Secondary panel: **Feed** (all), **For review** (unassigned or assigned to me / my teams),
  then **By source**: Slack, Google Drive, Google Docs, Google Sheets, GitHub, Custom, Police;
  **By rule**: Credentials (credential, token, private_key, password), Personal data (email,
  phone, address, national_id), Payment cards; then **Views** (saved and starred views with
  live counts) and "All views".
- Saved views store query, sort and page filters; unsaved changes show an accent dot on a
  "Save" split button ("Save as new view", "Reset"). Views are stored server-side per member
  (follow-up API) and fall back to `localStorage` until the API exists.
- Page layout grid: `page-filters search sort` on wide containers, wrapping on narrow ones.
- Default query: `is:unresolved severity:[high, medium]`. Query keys: `is:` (unresolved,
  acknowledged, in_progress, resolved, ignored, regressed), `severity:`, `rule:` (rule class),
  `source:`, `collector:`, `assigned:` (me, my_teams, none, member handle), `has:run`,
  `has:evidence`, `first_seen:`, `last_seen:`, `times_seen:`, free text on title and locator.
- Sort options (each with a detail line): **Last seen** (default, "Last time the item was
  found"), **First seen** ("When the issue was first reported"), **Trending** ("Findings
  increasing recently"), **Findings** ("Number of findings"), **Severity** ("Highest severity
  first"), **SLA due** ("Human tasks closest to breach").
- Columns, revealed progressively by container queries:

  | Column | Width / align | Visible from container |
  |--------|---------------|------------------------|
  | Issue (checkbox + summary) | flex | always |
  | Last seen | 86px right | `sm` |
  | Age (first seen) | 50px right | `2xl` |
  | Trend (24h / period toggle in header) | 175px | `xl` |
  | Findings (times seen) | 60px right | `lg` |
  | Resources (distinct locations) | 60px right | `2xl` |
  | Priority | 64px | `4xl` |
  | Assignee | 66px right | `sm` |

- Row anatomy: min-height 82px, whole row clickable (stretched link), title 16px medium with
  ellipsis (rule class label + masked display title), message line with `SeverityBar` and the
  masked display locator, meta row 12px secondary separated by thin vertical rules:
  `TELE-123` with source icon, location (`#channel`, Drive path, `repo:path`), an
  "Externally shared" / "Public" flag in `--danger-content` when the collector reports it,
  status tag when not unresolved, comment count, linked run indicator. Unread dot (8px accent)
  in the checkbox column. Counts use tabular numbers with a tooltip ("Matching filters",
  "Total in period", "Since first seen"). Shift-click selects a range, Cmd/Ctrl-click toggles.
- Bulk action bar (sticky, min-height 36px, `--bg-secondary`, top radius 6): select-all
  checkbox with indeterminate state; on selection the column headers fade out and actions
  appear: **Resolve** (split), **Ignore** (split: forever, for a duration, until regressed),
  **Acknowledge**, **Assign to**, **Start workflow**, more menu (mark in progress, reopen).
  "Select all N issues matching this search" banner; bulk operations on more than one page
  confirm in a modal.
- Empty states: no matches (with filter and syntax tips); zero unresolved ("Nothing exposed
  right now") with a link to Collectors.

### 5. Issue detail (`/issues/:id`)

- Breadcrumb: `Issues` > source icon + copyable `TELE-123`.
- Header block: title (20px, ellipsis with tooltip); right-hand counters **Findings** and
  **Resources** with their counts at 20px; status subtitle row: status text with tooltip,
  rule class, masked display locator, badges (evidence count, active run).
- Action bar (`role="toolbar"`, bottom border): left **Resolve** (split: "Mark resolved",
  "Resolved by removal" is shown read-only when auto-resolved), **Ignore** (split: forever,
  for duration, until regressed), **Acknowledge**, **Start workflow**, more menu (copy issue
  as Markdown `mod+alt+c`, rescan now); right **Priority** and **Assignee** selectors. When
  resolved or ignored the left actions become **Reopen** and the bar shows a status banner
  ("Resolved", "Ignored until regressed") with a success-tinted gradient.
- Two columns at container width >= 1152px: main `minmax(100px, 100%)` plus a 325px sidebar
  with "Open sidebar" / "Close sidebar"; below that the sidebar stacks under the main column.
- Main column, top filter sheet ("Narrow your focus"): collector/environment selector, time
  range ("Since first seen"), finding search (same token builder), and the **findings graph**
  (bar chart with "N findings" / "N resources" toggle; markers for scans, sweeps and workflow
  status changes), then **tag bars** (top values of detector, rule id, source, location
  segment) with "View all distributions" opening a drawer.
- Finding navigation (sticky; background fades in while stuck): tabs **Findings**,
  **Evidence**, **Runs**; **Recommended / First / Latest** segmented control; previous / next
  buttons; "View all findings".
- "Jump to" chip row and fold sections, in order:
  1. **Highlights**: rule, detector, confidence, severity, location, first/last seen.
  2. **Location chain** (replaces Sentry's stack trace): `workspace > channel > message`,
     `drive > folder > file > sheet!cell`, `org > repo > ref > path:line`; the matched frame
     expanded with masked context lines in mono 12px, line numbers right aligned, the matched
     line highlighted with `--bg-secondary`; `Redacted` spans for every masked value.
     Display options: "Matched location" / "All locations", newest / oldest.
  3. **Timeline** (`Timeline` component): history entries (created, regressed, assigned,
     status changes, scans, auto-resolve) with icons and timestamps; drawer for the full list.
  4. **Evidence**: accepted and rejected uploads (rejected show rule classes only), upload
     button with the scan-and-reject result.
  5. **Detection details**: detector, rule id, confidence, fingerprint prefix.
  6. **Masking details**: which masking methods were applied (no values).
- Sidebar sections separated by rules: **First / last seen** (relative times, scan job,
  collector), **Workflow runs** (each run with workflow name, current step, `StatusIndicator`,
  SLA due, and the `ProgressMarker`), **Escalation** (current tier, next escalation time),
  **Activity** (comment box "Add a comment..." with Markdown, chronological entries with
  progress markers, "View all activity" drawer, toasts "Comment posted" / "Comment removed"),
  **People** (assignee, participants).

### 6. Other pages

| Page | Sentry pattern | Telescreen specifics |
|------|----------------|----------------------|
| Search (`/search`) | Token search + page filters + table + cursor pagination | Global search across issues, findings, runs and audit indexes; index tabs; "Ask Assistant" toggle for natural-language queries |
| Workflows (`/workflows`, `/workflows/:name`) | Alert builder ("When any of the following... / If-Then blocks") + detail page | List: name, version, active, trigger summary, last run, runs in period. Detail: YAML/JSON editor (mono, line numbers, inline validation errors with JSON paths styled like invalid search tokens) side by side with a read-only **graph preview** of steps (step type icons, branches, send-back edges); version list; Activate / Deactivate; Validate |
| Runs (`/runs`, `/runs/:id`) | History table + step list with status glyphs (assistant tool-call style) | List: started, workflow, issue, status, current step, SLA due. Detail: ordered step list (status glyph: spinner -> success / danger / waiting clock; title; visit count; collapsible masked input and output); send-back transitions drawn as return arrows; waiting human task card with complete form; map step progress bar (shards succeeded / failed / in flight) |
| Police (`/police`) | Monitors list and detail with health dots | Columns: name, mode (pull / push), status dot, last heartbeat age, task types, labels, in-flight tasks, version. Detail: heartbeat timeline, recent tasks with results, disable action |
| Collectors (`/collectors`) | Monitors list + feature badges | One row per collector config: kind icon, trigger mode, last scan, findings in period, errors, latest sweep progress; "Run now" and "Sweep now"; empty state "Connect a source" |
| Escalation policies (`/escalation`) | Alerts list -> detail -> builder | List: name, tiers, used by workflows, last escalation. Detail: tier editor (after, assign to, notify, channels) as blocks; history of escalations |
| Audit (`/audit`) | Audit log table + filters | Columns: actor (member / token / system / police / collector), action, target, summary (masked), time; time range and action filters; chain verification banner ("Chain verified up to #N" or the first broken sequence) |
| Assistant (`/assistant`; `/chat` redirects; docked panel deferred, see section 8) | Assistant chat pattern | Right-aligned user bubbles (max 80% width), full-width assistant messages, collapsible "tool calls" blocks with status and links to issues / runs; docked right panel opened with `mod+/` carrying the current page context (issue id or view) |
| Settings (`/settings`) | Settings secondary nav + forms | Sections: Account (API token entry, theme System / Light / Dark), Members and teams, API tokens, Collectors credentials (secret refs only), About |

### 7. Implementation guidance

- Styling: plain CSS with the token custom properties; one CSS module per component under
  `src/web/src/components/ui/`; no CSS-in-JS runtime. Container queries
  (`container-type: inline-size`) for list columns and the issue-detail sidebar.
- The existing pages (p12-p14) keep their data flow and API client; this work replaces the
  shell, styles and presentational components and restructures page markup to the patterns
  above. Behavior covered by existing tests must keep passing; tests are updated where markup
  changes.
- Tests: vitest + `@solidjs/testing-library` component tests per new component (keyboard
  interaction, aria attributes, token-builder parsing and serialization); page tests with a
  mocked fetch. A visual smoke check renders the main pages headlessly in light and dark
  themes against fixture data from the synthetic test dataset
  (`design-synthetic-test-dataset.md`).
- Fixture data for the dev server: a mock API mode (`VITE_TELESCREEN_MOCK=1`) serving masked
  fixture issues, runs, police workers and collectors, so the UI can be developed and
  reviewed without the backend.

### 8. v1 implementation boundaries (run B, 2026-10-09)

Run B builds the UI against the accepted backend (commit 28ce81b) and adds no API endpoint,
query parameter or response field. A pattern from sections 4-6 that needs data the API does
not return is omitted in v1, not emulated with per-row requests or client-side guesses.
The authoritative API shapes are the router serializers in `src/telescreen/api/routers/`
(not `src/web/src/api/types.ts` as accepted with p07, which drifted; see 8.1a).

#### 8.1 API capability mapping

| UI element | v1 behavior (API source) |
|------------|--------------------------|
| Issue search tokens | Supported keys map one-to-one to `GET /api/issues` parameters: `is:` -> `status`, `severity:`, `rule:` -> `rule_class` (one `RuleClass` value: credential, token, private_key, password, payment_card, national_id, email, phone, address; the "By rule" groups of section 4 become collapsible link groups with one link per class), `source:`, `assigned:` -> `assignee` (a recipient ref `member:<handle>` or `team:<slug>`; the API answers an unknown ref with an error shown as a banner), free text -> `q`. One value per key; `assigned:me|my_teams|none`, `!`, `*`, `[a, b]`, comparisons and the keys `collector`, `has`, `first_seen`, `last_seen`, `times_seen` render as invalid tokens whose tooltip lists the supported keys, and invalid tokens are not sent. Default query `is:unresolved` |
| Sort | Last seen, First seen, Findings (`sort=last_seen|first_seen|times_seen`). Trending, Severity and SLA due are omitted |
| Page filter bar | Source segment only (same as `source:`). Collector and date range segments are omitted (no API filter) |
| Secondary panel (Issues) | Feed, By source, By rule, Views. "For review" is omitted (the API cannot filter unassigned or "me") |
| URL state | `/issues?query=<token text>&sort=<key>` is the single source of truth for the list; secondary-panel links and saved views are such URLs |
| Saved views | `localStorage` key `telescreen.issueViews`, JSON array of `{id, name, query, sort, starred}`, written by the Issues page and read by the shell panel. No live counts |
| List columns | Issue, Last seen, Age, Findings (`times_seen`), Assignee, Priority (read-only, derived from `severity`: high -> high, medium -> medium). The per-row Trend column is omitted (no per-issue series exists; the p07-era per-row stats call targets a route that does not exist and is removed). Above the list, one all-issues 14-day `MiniBarChart` from the global `GET /api/issues/stats` (`{buckets: [{start, count}]}`, `bucket=day`, `days=14`) keeps the count trend. Resources, comment count, unread dot and "Externally shared" flag are omitted |
| Bulk actions | Resolve, Ignore, Acknowledge, Mark in progress, Reopen and Assign to run one `PATCH /api/issues/{id}` per selected issue (only transitions allowed by the data-model status machine are offered); per-issue failures are reported in a toast with counts. Ignore has no duration or until-regressed options. Start workflow calls `POST /api/workflows/{name}/runs` per issue (workflow picker over active workflows). Select-all covers loaded rows only; the "Select all N matching" banner and the multi-page confirm modal are omitted (no total count) |
| Issue detail header | Findings = `times_seen`; Resources = distinct `location` values among loaded events. Badges: evidence count (`GET /api/evidence?issue_id`), active run (`GET /api/runs?issue_id`) |
| Issue detail actions | Resolve, Ignore, Acknowledge, Reopen, Assignee (`PATCH`), Start workflow, copy as Markdown (client-side from masked data). "Rescan now" and Set priority are omitted |
| Findings graph | One series built from the first page of `GET /api/issues/{id}/events?limit=200`: `observed_at` bucketed by UTC day over the last 14 days, labelled "Loaded findings, last 14 days"; no resources toggle and no markers |
| Tag bars | Computed from loaded events: `detector` and the first `location` segment; `source` and `rule_class` from the issue |
| Filter sheet | Finding search and collector/time selectors are omitted; finding navigation (Recommended = highest confidence then latest, First, Latest, previous/next) works over loaded events |
| Location chain | The event `location` string split into frames on ` > `, `/` and `:`; the matched frame shows `masked_snippet` (no extra context lines exist) |
| Fold sections | Highlights, Location chain, Timeline (`/history`), Evidence, Detection details (detector, `rule_id`, rule class, confidence, first 8 characters of `fingerprint`). Masking details is omitted (methods are not in the API) |
| Sidebar | First/last seen; Workflow runs (`GET /api/runs?issue_id`: workflow name via the `GET /api/workflows` id map, status, started; `ProgressMarker` from issue status: unresolved/regressed -> Identified, acknowledged -> Assigned, in_progress -> Contained, resolved -> Remediated); for the latest running or waiting run only, one `GET /api/runs/{id}` gives the current step (latest step run not in a terminal status) and the Escalation section (open human task title, assignee and status; tier and due time are omitted because the run detail does not return them); Activity (`/history` entries rendered from `kind`, `actor_ref`, `from_value` -> `to_value`, `note`, `at`, plus comments posted in this session; `POST /comments` returns `{accepted: true}` and there is no comment list endpoint); People (assignee resolved through `GET /api/members`) |
| Workflows | List: name, version, active (`GET /api/workflows` items are versions `{id, name, version, active}`; grouped by name). Detail (`GET /api/workflows/{name}` returns that name's versions; `GET /api/workflows/{name}/versions/{v}` the source): editor, validate, versions, activate; the graph preview parses the editor text client-side (JSON native, YAML with the `yaml` package) and draws steps with edges from `next`, `outcomes`, `cases`/`default`, `branches`/`join`, `on_error`, `on_exhausted`, and dashed return edges for `send_back.to`. Invalid text shows "Fix validation errors to preview". Trigger summary, last run and runs in period are omitted |
| Runs | List: started, workflow (name via the `GET /api/workflows` id map from `definition_id`), issue, trigger, status. Current step and SLA due are omitted in the list. Detail (`{run, steps, transitions, human_tasks}`): step list as section 6 with status glyphs for `ready/running/waiting/succeeded/failed/cancelled`, `visit`, `attempt`, `error_code`, masked input/output; step types come from the run's workflow version (`GET /api/workflows/{name}/versions/{v}` parsed with the graph-preview parser); send-back return arrows from transitions of kind `send_back` (`from_step_run_id` -> `to_step_id`); waiting human task card with the `outcomes` completion form (no due time). The map progress bar is shown when a `map` step's `output_json` has a string member `map_job_id` (`GET /api/map-jobs/{id}`); otherwise the step renders without it |
| Police | List columns from `GET /api/police/workers` (no in-flight count, no contract version). Status dot: `disabled` -> muted; `active` with `last_heartbeat_at` within 5 minutes -> success; otherwise warning labelled "Stale". Detail drawer shows the worker fields and Disable; heartbeat timeline and recent tasks are omitted |
| Collectors | Kind, name, trigger mode, cron, enabled, latest sweep status (one `GET /api/map-jobs` call, latest item per `collector_config_id` by `created_at`; progress counts only in a drawer via `GET /api/map-jobs/{id}`), Run now, Sweep now (returns `{map_job_id}`). Enable/disable and edits send the full collector body. Last scan, findings in period and errors are omitted |
| Escalation | List: name, tier count (parsed from `tiers_json`). Detail: tiers parsed from `tiers_json` rendered as read-only blocks, plus a JSON source editor submitted as `{tiers_json, source_format: "json"}` (POST to create, PUT `/{name}` to update) |
| Audit | As section 6 with filters `q`, `action`, `actor`, `target_kind`, `from`, `to`, offset pagination over `{items, total}` and `GET /api/audit/verify` |
| Assistant | Page at `/assistant` (page directory stays `pages/chat`). `mod+/` and the TopBar "Ask Assistant" button navigate to `/assistant?context=<current path>`; the page shows the context as a chip and prefixes it to the first message. Answers render as text (the API returns no tool-call trace) |
| Search | As section 6; "Ask Assistant" links to `/assistant?q=<query>` |
| Settings | Account (token in `sessionStorage`, theme), Members and teams (`/api/members`; `/api/teams` items `{id, slug, name, email, slack_channel}`; membership is set with `PUT /api/teams/{slug}/members {handles}` and is not listed), API tokens (no list endpoint: text pointing to `telescreen token create`), Collector credentials (`credential_ref` values from `/api/collectors`), About (`/api/health`) |

#### 8.1a Client alignment with the router serializers (binding, p31)

`src/web/src/api/types.ts` and `client.ts` must describe exactly what the accepted routers
return; mock fixtures (8.3) use the same serializer shapes, never the old `types.ts` shapes.
Known drift to fix (checked against `src/telescreen/api/routers/*.py` at 28ce81b):

| Area | Alignment |
|------|-----------|
| Issues | `IssueEvent` = `_finding_json` (`observed_at`, `rule_id`, `rule_class`, `display_locator`, `fingerprint`, ...); `IssueHistory` = `{id, issue_id, kind, actor_ref, from_value, to_value, note, at}`; `Issue` adds the extra serializer fields; `issuesApi.stats` becomes the global `GET /api/issues/stats?bucket&days` -> `{buckets: [{start, count}]}`; `comment` returns `{accepted: true}` |
| Lists | Runs, human tasks, workflows, evidence, escalation and collectors return `{items}` only (the client normalizes to `next_cursor: null`); audit returns `{items, total}` with `limit`/`offset`; search `snippet` is nullable |
| Workflows | List and `GET /{name}` items `{id, name, version, active}`; version adds `id`, `compiled_json`, no `created_at`; schema is a raw JSON Schema document (text); create, activate return `{id, name, version, active}`; create failure is 422 `{valid, errors}` (decoded as validation errors); start run returns `{run_id}` |
| Runs | Summary `{id, definition_id, issue_id, status, trigger, started_at, finished_at}`; detail `{run, steps, transitions, human_tasks}` with the step, transition and human-task fields of `runs.py`; cancel returns `{run_id, status}`; statuses `RunStatus` running/waiting/succeeded/failed/cancelled and `StepRunStatus` ready/running/waiting/succeeded/failed/cancelled |
| Human tasks | Items `{id, run_id, step_run_id, kind, title, assignee_ref, due_at, status, outcome}`, status open/completed/cancelled; complete returns `{id, status}` |
| Evidence | Adds `sha256`, no `run_id`; rejection error carries `rule_classes` |
| Escalation | `{id, name, tiers_json}`; create/update body `{tiers_json, source_format}` |
| Directory | Teams `{id, slug, name, email, slack_channel}`; set members body `{handles}`, 204 |
| Collectors | Create/patch take the full body (including nullable `cron`, `sweep_cron`, `credential_ref`) and return `{id}`; run returns `{id}`; sweep returns `{map_job_id}`; the webhook helper is removed (the UI never calls it) |
| Map jobs | List items `{id, origin, status, collector_config_id, created_at}`; detail `counts` holds present statuses only; cancel/retry return `{id, status}` |
| Police | Workers have no `contract_version`; status active/disabled; disable returns `{id, status}` |

Wave handoff: because aligning the types changes fields the existing pages read, p31 also
makes the mechanical call-site and test-fixture updates in `src/web/src/pages/**` needed to
keep `bun run typecheck` and `bun run test` green (renames, removed fields dropped from the
display, response bodies re-fetched instead of used). No visual or structural page change.
This is an explicit wave-1 -> wave-2 handoff (architecture.md section 8): p31 runs while
no page plan is active; from wave 2 the page directories belong only to p12/p13/p14.
p31 keeps the signatures of `ApiError`, `apiFetch`, `getToken` and `setToken`, which the
p30-owned `ErrorBanner` and `shell.test.tsx` import, so the two wave-1 plans stay
independent.

#### 8.2 Shell and route contract

- Routes keep the p07 contract: `AREAS` keys `issues, search, workflows, runs, police,
  collectors, escalation, audit, chat, settings`; each page is the default export of
  `pages/<key>/index.tsx`; `<path>` and `<path>/:id` routes; `/` redirects to `/issues`. The
  `chat` area path becomes `/assistant`, with `/chat` and `/chat/:id` redirecting.
- Pages render their title and actions only through `TopBar.Slot` (`name="title"` renders
  the single `<h1>`, `name="actions"`) exported by the shell; pages never render an `<h1>`.
- The shell owns the secondary panel content of every area (static links, for Issues the
  URLs of section 8.1 plus saved views from `telescreen.issueViews`).
- Shared components keep their paths and props: `ErrorBanner`, `JsonTextArea`, `LoadMore`,
  `RelativeTime`, `Sparkline`, `StatusBadge`, `Unavailable` are restyled in place on tokens
  (`StatusBadge` renders `Tag`, `Sparkline` renders `MiniBarChart`) so pages compile before
  and after their rebuild. Only `Layout` and `Sidebar` are removed, replaced by the shell
  under `components/shell/`.

#### 8.3 Mock API mode

- Switch: `import.meta.env.VITE_TELESCREEN_MOCK === "1"`, checked inside `apiFetch` and
  the evidence blob fetch in `src/api/client.ts`. The mock module is loaded with a dynamic
  `import()` inside that branch, so production builds (flag unset) do not contain fixtures.
  Pages and tests keep calling the same `*Api` objects.
- `src/api/mock/` holds a router (method + path pattern -> handler) over in-memory state
  seeded from fixtures. It returns the router serializer shapes of 8.1a and the error model (`404
  not_found` for unknown paths; police v1, ingest and webhook paths are not mocked).
  Mutations (PATCH issue, comment, human-task complete, activate, cancel, run, sweep,
  disable, escalation edits) change in-memory state for the page session only. Evidence
  upload never reads the file body and returns an accepted record with a synthetic display
  filename. Chat returns canned answers built from fixture counts. No token is required.
- Coverage: every endpoint the pages call returns data; at least one issue per status and
  per source, a regressed issue, a run per run status including a waiting human task with
  SLA, a send-back transition and a `map` step with a running map job, police workers
  online/offline/disabled in pull and push mode, one collector per kind, two escalation
  policies, audit records with a verified chain, search hits for every index.
- Timestamps are offsets from the module-load time, so relative times are stable.
- Value rules (`design-synthetic-test-dataset.md`): fixtures contain only masked forms in
  the exact Masker output formats (`design-masking.md`, Masking policy) and reserved
  example domains. A vitest test serializes all fixtures and fails on any raw-looking value
  (AWS `AKIA` + 16, `ghp_` + 36, `xox[abp]-` bodies, `AIza` + 35, PEM bodies, three-segment
  JWTs, Luhn-valid 13-19 digit runs, 12-digit runs, emails outside example domains).
  `tests/leak` also runs the rule detector over `src/web/src/api/mock/**` and asserts zero
  findings.
- The TopBar shows a muted "Mock data" tag when the flag is set.

#### 8.4 Fonts, icons and dependencies

- `src/web/public/fonts/`: Rubik 400/500 and Roboto Mono 400/500 woff2 (latin subset or
  variable files) plus `OFL-Rubik.txt` and `OFL-RobotoMono.txt`; `@font-face` with
  `font-display: swap` in `styles/`; no runtime font request.
- `src/web/package.json` additions (single owner): `lucide-solid` (ISC), `yaml` (ISC, graph
  preview), dev `playwright-core` (Apache-2.0, screenshot smoke; no bundled browser).

#### 8.5 Screenshot smoke

- `bun run screenshots` (script under `src/web/scripts/`) builds with
  `VITE_TELESCREEN_MOCK=1`, serves the build with `vite preview` on a local port, and drives
  a system Chrome/Chromium through `playwright-core` (`TELESCREEN_SCREENSHOT_BROWSER` path,
  else auto-detected). Pages: `/issues`, one issue detail, `/search?q=...`, `/workflows`,
  one workflow, `/runs`, one run, `/police`, `/collectors`, `/escalation`, `/audit`,
  `/assistant`, `/settings`, at 1440x900 in light and dark (`colorScheme` emulation), plus
  `/issues` at 390x844. Output: `<repo>/tmp/ui-screenshots/<page>-<theme>.png` (gitignored).
  The script fails on any page error or console error. It is not part of `bun run test`,
  `bun run build` or `mise run ci`; when no browser is available it exits non-zero with a
  message and the gap is reported as a verification gap.

#### 8.6 Serving the built UI (`telescreen serve`)

- The UI is located only through `TELESCREEN_WEB_DIST` (absolute or working-directory
  relative path to a `vite build` output). The wheel does not contain UI assets, so
  `uv build` needs no node toolchain. Local use: `cd src/web && bun run build`, then
  `TELESCREEN_WEB_DIST=src/web/dist telescreen serve`; the compose image sets
  `/app/web`.
- When set, the directory must contain `index.html`, else `serve` exits 3 (configuration
  error). Files are served at `/`; any `GET` that is not under `/api/` and matches no file
  returns `index.html` (SPA fallback). Paths under `/api/` never fall back (unknown ones
  return the JSON `not_found` error). `index.html` is sent with `Cache-Control: no-cache`.
  No new Python dependency (Starlette static files).
- When unset, only the API is served.

#### 8.7 Plan boundaries (run B)

| Plan | Owns | Wave |
|------|------|------|
| p30-ui-design-system | `src/web/package.json`, `src/web/bun.lock`, `src/web/public/fonts/**`, `src/web/scripts/**`, `src/web/src/styles/**`, `src/web/src/components/**` (ui, shell, restyled shared components), `src/web/src/{App,routes,main}.tsx`, `src/web/src/shell.test.tsx` | 1 |
| p31-ui-mock-api | `src/web/src/api/**` (serializer alignment 8.1a, client mock switch, `mock/**`, env typing), plus the 8.1a mechanical handoff edits in `src/web/src/pages/**` | 1 (no conflict with p30, which does not touch pages) |
| p12 / p13 / p14 | Their existing page directories (unchanged) | 2 |
| p28-assembly | As accepted plan, plus the static serving rules of 8.6 and the fixture leak test of 8.3 | 2 (parallel with p12-p14; the serve tests use a temporary dist directory, not a real build) |
| p29-examples-compose | `examples/compose/**`, `examples/workflows/**`, `tests/examples/**`, `README.md` | 3 |

### 9. v1.1 additions (binding for the v1.1 run)

This section extends section 8 for the v1.1 features (`design-v1-1-dspm-adoption.md`)
against the "v1.1 endpoints" of `design-api-and-ui.md`. Where a row below differs from
8.1, this section wins. The rule of section 8 still applies: data the API does not return
is omitted, not emulated.

#### 9.1 Tokens and components

- New color role `--critical` / `--critical-strong` / `--critical-content` /
  `--critical-muted-bg`:
  - light: `#B4232A` / `#8C1A20` / `#8C1A20` / `#B4232A1F`;
  - dark: `#F0646A` / `#5E1A1D` / `#F7B5B7` / `#F0646A33`.

  Use: critical severity only.
- `SeverityBar`: critical `--critical`, high `--danger`, medium `--warning`.
- `PriorityBadge`: 4/3/2 bars for critical/high/medium. It is still derived read-only from
  the effective `severity`.
- `Tag` gains variant `critical`.
- New `ExposureBadge` (a `Tag`):
  - `public` -> danger "Public";
  - `anyone_with_link` -> danger "Link sharing";
  - `external_shared` -> warning "Shared externally (N)", or without a count when N is 0;
  - `domain` -> muted "Domain";
  - `internal`, `private` and `unknown` render nothing in list rows and plain text in the
    detail sidebar.
- New `RiskPill`: the score in tabular numbers on `--bg-tertiary`, with a tooltip "Ordinal
  risk 0-100: severity x exposure x asset x age".
- Status tags:
  - `allowed` -> muted "Allowed";
  - `pending_verification` -> info "Verifying";
  - `resolved` with resolution `manual_unverified` -> warning "Resolved (unverified)" with a
    warning icon;
  - `ignored` -> muted "Ignored", plus the expiry text from the active exception or "No
    expiry (legacy)".
- `ProgressMarker`: `pending_verification` -> Fix proposed. `allowed` shows no marker.

#### 9.2 Shell and routes

- `AREAS` gains `tasks` (path `/my-tasks`, page `pages/tasks/index.tsx`, rail label "My
  tasks", placed after Issues) and `policies` (path `/policies`, page
  `pages/policies/index.tsx`, rail label "Policies", placed after Collectors).
- Rail order: Issues, My tasks, Search, Workflows, Police, Collectors, Policies,
  Escalation, Audit, Assistant, Settings.
- The My tasks rail item shows a count badge from the open human tasks plus the requested
  approvals.

#### 9.3 API capability mapping (v1.1 rows)

| UI element | v1.1 behavior (API source) |
|------------|----------------------------|
| Issue search tokens | Adds `is:allowed`, `is:pending_verification`, `exposure:<visibility>` -> `exposure`, the shorthand `is:public` -> `exposure=public`, and `assigned:me` / `assigned:my_teams` -> `assignee=me|my_teams`. `severity:` accepts `critical`. Other limits of 8.1 are unchanged |
| Sort | Adds **Risk** ("Highest risk first", `sort=risk`) |
| Secondary panel (Issues) | "For review" is restored as `assigned:me`. Views gain "Allowed" (`is:allowed`) and "Verifying" (`is:pending_verification`) |
| List columns | Adds Risk (`RiskPill`, 48px right, from container `md`). The meta row shows `ExposureBadge` from `exposure_visibility` (replacing the omitted "Externally shared" flag) |
| Bulk actions | Acknowledge, Mark in progress, Reopen, Assign to, Start workflow, and **Mark remediated** (`POST /remediation {action: remove}` per issue). Resolve and Ignore are no longer bulk actions, because both need a reason |
| Issue detail actions | **Resolve** split: "Mark remediated..." (dialog: removed or restricted; "Include linked occurrences" checkbox for credential classes), "Resolve as false positive..." (note required), "Resolve without verification..." (admin only, warning styling). **Ignore** becomes "Request exception..." (reason required; duration 7, 30 or 90 days or until regressed; "Forever" only for admin tokens; an admin request is approved at once). Reopen calls PATCH `unresolved` |
| Status banner | `pending_verification`: "Verifying: waiting for a rescan of the resource". `allowed`: "Allowed by policy <name> v<version>". `ignored`: "Ignored until <date>, approved by <ref>" / "Exception requested, waiting for <ref>" / "No expiry (legacy)" |
| Sidebar | Adds **Exposure** (visibility, external principal count, shared drive, channel kind, repo visibility, fork, changed at), **Risk** (score, severity with base severity when different, asset tier, age), **Owner** ("Mapped to <member>" / "Owner not mapped" / "No owner"), **Policy** (name, version, effect) and the **Exception** card with Approve/Reject when the viewer is the approver |
| Fold sections | "Linked occurrences (N)" after Location chain (`GET /occurrences`): rows link to the other issues and show source, masked display locator, status and exposure. A note says that links are limited to fingerprints of one key version and that a key rotation starts new groups |
| Timeline | Renders the new history kinds with icons: exposure changed, policy re-evaluated, verification requested/failed/inconclusive, exception requested/approved/rejected/revoked/expired |
| My tasks (`/my-tasks`) | Four panels: **Owner reviews** (`GET /api/human-tasks?assignee=me&status=open`, kind `owner_review`: outcome buttons Remove / Restrict / Exception / Not mine, comment field, duration select for Exception); **Other tasks** (other open human tasks, linking to the run); **Approvals** (`GET /api/exceptions?approver=me&status=requested`: Approve / Reject); **Assigned issues** (`GET /api/issues?assignee=me&sort=risk`, open statuses, with a Verifying group). Empty state "Nothing needs your attention" |
| Policies (`/policies`, `/policies/:name`) | List: name, active version, effect, enabled, owner team. Detail mirrors Workflows: YAML/JSON editor with server-side validate and an error list, versions, Activate, Disable (admin). No graph preview |
| Collectors | Adds a health column (`StatusIndicator`: ok success, warning warning, failing danger, unknown muted; tooltip lists conditions and last success) and an "Event only" warning tag. Detail `/collectors/:id` gains tabs **Overview** / **Coverage**. Coverage shows counts by state and reason, plus the **Unscannable resources** table (resource id prefix, state, reason, `ExposureBadge`, last attempt, linked issues) with cursor pagination. Public rows sort first |
| Police | The status dot uses `health.status` from the API, replacing the client-side 5-minute rule; the tooltip shows `police_silent` when open |
| Escalation | Tier blocks show `min_risk` when set; the JSON editor accepts it |
| Settings | Members: an identities list per member (kind, origin, key prefix), "Add GitHub login or email" (admin; the value is never shown again) and remove. Teams: a lead selector (`lead`) |

#### 9.4 Mock API and screenshots

- The mock router serves every v1.1 endpoint in 9.3 with in-memory mutations: remediation,
  resolve, exceptions (request, approve, reject, revoke), policies (validate, create,
  activate, disable), identities and team lead.
- Fixture coverage:
  - at least one issue per v1.1 status and per visibility;
  - a critical issue;
  - a `manual_unverified` resolution;
  - a credential issue with two linked occurrences;
  - risk scores across the range;
  - two policies (allow with conditions, violate) with two versions;
  - exceptions in each status;
  - an open `owner_review` task assigned to the mock member;
  - coverage rows of each state including a public unscannable one;
  - collectors in each health status, including event-only;
  - a silent pull police worker.
- The fixture value rules of 8.3 still apply: no raw values. Fixtures also contain no real
  file names or emails outside reserved example domains, and identity rows show key
  prefixes only.
- The screenshot smoke (8.5) adds `/my-tasks`, `/policies`, one policy, and one collector
  detail with `?tab=coverage`, in light and dark (35 images in total). It must finish with
  zero page or console errors.

#### 9.5 Plan boundaries (v1.1 run)

| Plan | Owns | Wave |
|------|------|------|
| v11-ui-foundation | `src/web/src/api/**` (types, client, mock router and fixtures), `src/web/src/components/**`, `src/web/src/styles/**`, `src/web/src/{routes,App}.tsx`, `src/web/src/shell.test.tsx`, `src/web/scripts/**` | 1 |
| v11-ui-issues | `src/web/src/pages/issues/**` | 2 |
| v11-ui-ops | `src/web/src/pages/{tasks,policies,collectors,police,escalation,settings}/**` | 2 |

`src/web/package.json` and `bun.lock` need no change. If a dependency turns out to be
needed, it is added by v11-ui-foundation only.

## Sources

| Topic | Source |
|-------|--------|
| UI refresh (S.C.R.A.P.S., chonky) | https://blog.sentry.io/sentry-has-a-bold-new-look |
| Streamlined issue details | https://sentry.io/changelog/new-issue-details-ui-now-available/ |
| New navigation and issue views | https://sentry.io/changelog/new-nav-issue-views-ga |
| Monitors and alerts | https://sentry.io/changelog/monitors--alerts--now-generally-available/ |
| Issues docs, views, states, details, search | https://docs.sentry.io/product/issues/ , https://docs.sentry.io/product/issues/issue-views , https://docs.sentry.io/product/issues/states-triage/ , https://docs.sentry.io/product/issues/issue-details/ , https://docs.sentry.io/concepts/search/ |
| Navigation source | https://github.com/getsentry/sentry/tree/master/static/app/views/navigation |
| Design tokens and theme | https://github.com/getsentry/sentry/tree/master/static/packages/scraps/src |
| Issue stream source | https://github.com/getsentry/sentry/tree/master/static/app/views/issueList , https://github.com/getsentry/sentry/blob/master/static/app/components/stream/group.tsx |
| Issue details source | https://github.com/getsentry/sentry/tree/master/static/app/views/issueDetails |
| Search query builder | https://github.com/getsentry/sentry/tree/master/static/app/components/searchQueryBuilder |
| Masked value rendering | https://github.com/getsentry/sentry/tree/master/static/app/components/events/meta/annotatedText |
| Alerts (automations) and audit log | https://github.com/getsentry/sentry/tree/master/static/app/views/automations , https://github.com/getsentry/sentry/blob/master/static/app/views/settings/organizationAuditLog/auditLogList.tsx |
| License | https://github.com/getsentry/sentry/blob/master/LICENSE.md , https://github.com/getsentry/sentry/blob/master/static/packages/scraps/README.md |
| Fonts and icons | https://fonts.google.com/specimen/Rubik , https://fonts.google.com/specimen/Roboto+Mono , https://lucide.dev/license |
