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

- Typography: `--font-sans: Rubik, "Avenir Next", system-ui, sans-serif`; `--font-mono:
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
  Escalation, Audit, Assistant, Settings; each item is a 32px square icon container (radius
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
| Assistant (`/assistant` and docked panel) | Assistant chat pattern | Right-aligned user bubbles (max 80% width), full-width assistant messages, collapsible "tool calls" blocks with status and links to issues / runs; docked right panel opened with `mod+/` carrying the current page context (issue id or view) |
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
