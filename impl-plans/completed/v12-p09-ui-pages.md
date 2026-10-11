# V12-P09 UI Pages: Settings (General, Templates, Users and Roles), Workflow Sets, Run Targets

**Status**: Completed
**Design Reference**: design-docs/specs/design-ui-sentry.md sections 10.2, 10.3, 10.5; design-docs/specs/design-api-and-ui.md "v1.2 endpoints"
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p09-ui-pages`
- planPath: `impl-plans/active/v12-p09-ui-pages.md`
- wave: 2
- dependsOn: `v12-p03-ui-foundation`
- writePaths:
  - `impl-plans/active/v12-p09-ui-pages.md`
  - `src/web/src/pages/settings`
  - `src/web/src/pages/workflows`
  - `src/web/src/pages/runs`
  - `src/web/node_modules` (artifact)
- sharedPaths: none
- artifactRoots: `src/web/node_modules`

Read `impl-plans/conventions.md` first.

## Intent and Context

Build the v1.2 pages on top of the clients, types, mock endpoints, routes and
`useAuth()` delivered by v12-p03:

- Settings > General;
- Settings > Notification templates (list, editor, validate, preview, activate);
- Settings > Users and roles (role select, last-admin error, permission matrix);
- the workflow definition "Notification sets" panel;
- per-target counts of v2 notify outputs in run detail.

Imitate:

- `src/web/src/pages/policies/` (list and detail with versions, validate, activate);
- `src/web/src/pages/workflows/WorkflowEditor.tsx` (editor + validate error list);
- `src/web/src/pages/settings/index.tsx` (current members/teams sections);
- `src/web/src/components/ui/*` (Button, Tag, FoldSection, EmptyState).

## Non-goals

- No change outside these three page directories. A missing client function or type
  goes to a Reconciliation request against p03; until it is resolved, use a local typed
  wrapper inside the page directory that calls `apiFetch`.
- No token management UI (design: API only). No new package.

## Invariants

- Pages render titles only through `TopBar.Slot` (design-ui-sentry 8.2).
- The Settings page no longer contains the API token entry (moved to `/login` by p03).
  It keeps the v1.1 member/team/identity sections under General.
- Editor actions are hidden unless `useAuth().can("templates.write")`; the role select
  is disabled unless `can("users.write")`.
- No raw-looking values in test data (the same rules as the fixtures).

## Deliverables

1. **`pages/settings/index.tsx`**: section switch from the route params:
   - `/settings` -> General (existing member, team and identity sections, minus token
     entry);
   - `/settings/templates` -> `TemplatesList.tsx`;
   - `/settings/templates/:name` -> `TemplateDetail.tsx`;
   - `/settings/users` -> `UsersRoles.tsx`.
2. **`TemplatesList.tsx`**: columns name, event kind, `Tag` "Built-in" or "Custom",
   active version, latest version, updated. A "New template" button (write only) opens
   the editor in create mode (name and event kind inputs).
3. **`TemplateDetail.tsx`**:
   - a version list with Activate (version 0 labeled "Built-in default");
   - an editor with subject, body, email subject, email body and Slack body textareas;
   - an "Allowed variables" side list from `variables` (click inserts `{{ path }}`);
   - Validate shows `{field, code}` errors next to the fields;
   - Preview shows Email and Slack tabs with DLP verdict tags;
   - "Save as new version" calls `create` and shows "Saved as version N (inactive)".
4. **`UsersRoles.tsx`**:
   - users table: handle, display name, role select (admin, responder, viewer, none),
     a "Managed by IdP" tag with tooltip text "The next sign-in resets this role from
     the identity provider.", teams, last sign-in (`RelativeTime`);
   - a 409 `last_admin` shows the inline error "At least one administrator must
     remain.";
   - below the table, the read-only permission matrix from `GET /api/roles` (rows:
     permissions grouped by the prefix before the dot; columns: roles, then scopes;
     cells: a check mark rendered with lucide `Check`, not an emoji).
5. **`pages/workflows/`**: a "Notification sets" `FoldSection` in the definition view.
   Parse `source_text` with the existing `yaml` dependency (as the graph preview does)
   and show:
   - one card per set, with target rows (recipient chips, channel tags, template
     name);
   - in the graph, notify and human-task nodes show `set: <name>` or "inline targets".
   - v1 workflows show nothing new.
6. **`pages/runs/RunDetail.tsx`**: when a notify step output has `targets`, render a small
   table: target, channel, queued, blocked, skipped.

## Pitfalls

- `/settings/templates/system.workflow_notify` contains a dot in the param. Do not split
  on `.`.
- Preview and validate send the editor's current text. Never persist drafts to
  `localStorage` (template text may be sensitive).
- YAML parse errors in the sets panel must render "Unable to read notification sets",
  not throw (screenshot smoke: zero console errors).
- Keep components small. If any file approaches 400 lines, split it.

## Tests (vitest, colocated)

- TemplatesList: renders built-in and custom rows from the mock; "New template" hidden
  without `templates.write`.
- TemplateDetail:
  - validate with an `unknown_variable` error renders the error next to the body;
  - preview renders both tabs;
  - activate version 0 calls `activate` with `{version: 0}`;
  - save calls `create` and shows the inactive notice.
- UsersRoles: changing the role calls `setRole`; a mocked 409 `last_admin` shows the
  inline text; the matrix renders role columns; the select is disabled without
  `users.write`.
- Workflows: the v2 fixture shows two set cards; the v1 fixture shows no sets section;
  invalid YAML shows the fallback text. The shared mock fixtures (`src/web/src/api/mock`,
  p03, committed) contain no v2 workflow, so define the v1 and v2 `source_text` YAML as
  local constants in the colocated test file (recipients only `slack_channel:C0123ABCDE`
  and `email:` addresses at `example.invalid`). Do not edit `src/web/src/api/mock`.
- RunDetail: a targets table is rendered for a v2 output.
- Settings General: no token input is present.

## Verification

From the repository root, with `set -o pipefail`:

```bash
cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v12-p09-ui-pages-install.log 2>&1; echo "exit=$?"
cd src/web && bun run typecheck > /tmp/telescreen-v12-p09-ui-pages-typecheck.log 2>&1; echo "exit=$?"
cd src/web && bun run test > /tmp/telescreen-v12-p09-ui-pages-test.log 2>&1; echo "exit=$?"
cd src/web && bun run build --outDir /tmp/telescreen-v12-p09-ui-pages-web --emptyOutDir > /tmp/telescreen-v12-p09-ui-pages-build.log 2>&1; echo "exit=$?"
```

All four commands must exit 0. The screenshot smoke for these pages is run by v12-p12.

## Completion Criteria

- [x] Deliverables 1-6 implemented.
- [x] typecheck, vitest and build exit 0.
- [x] No files changed outside the three page directories (`git status --porcelain`).
- [x] Progress Log complete.

## Progress Log

### Session 9 (2026-10-11, d3, base 8ff064f, implementer Claude in place of Codex Luna)

- Dependency: v12-p03-ui-foundation is in acceptedPlanIds. Clients (`templatesApi`,
  `usersApi`), types, mock routes and `useAuth()` from p03 were used as is; no
  Reconciliation request was needed.
- Settings (`pages/settings/`): `index.tsx` is a section switch on `{id, sub}`; the template
  name is the whole decoded `:sub` segment (dots kept). `GeneralSettings.tsx` holds the v1.1
  sections without the token entry. `TemplatesList.tsx`, `TemplateDetail.tsx`,
  `TemplateEditor.tsx` (create and version modes, allowed-variable insert at the cursor,
  per-field `{field, code}` errors, drafts in memory only), `TemplatePreview.tsx` (Email and
  Slack tabs, DLP verdict tags), `templateText.ts`, `UsersRoles.tsx` (role select, Managed by
  IdP tooltip, inline `last_admin` text, select restored on failure), `PermissionMatrix.tsx`
  (grouped by prefix, roles then scopes, lucide `Check`), `settings.css`, `testUtils.tsx`.
  Save, Activate and New template need `templates.write`; Validate and Preview stay visible
  because the API grants them with `templates.read`.
- Workflows: `sets.ts` (never-throwing parser and `notifyLabel`), `NotificationSets.tsx`
  (fold section, one card per set, fallback "Unable to read notification sets"); `graph.ts`
  nodes carry an optional `notify` label (`set: <name>` or "inline targets" for notify,
  human_task and evidence_upload) drawn by `WorkflowGraph.tsx`; `WorkflowEditor.tsx` mounts
  the panel. v1 sources render nothing new.
- Runs: `NotifyTargets.tsx` renders target index, channel, queued, blocked, skipped from a
  v2 step output `targets`; v1 outputs render nothing.
- Tests: TemplatesList (3), TemplateDetail (5), UsersRoles (4), Settings General (no token
  input), NotificationSets (5, local v1/v2 YAML constants), RunDetail (v2 targets table).
- Verification (final source): install exit 0 (no changes), typecheck exit 0, vitest exit 0
  (36 files, 183 passed), build to /tmp exit 0, package.json/bun.lock diff empty. Logs:
  `/tmp/telescreen-v12-p09-ui-pages-{install,typecheck,test,build}.log`, copies under
  `tmp/telescreen-v12-20261011-d3/v12-p09-ui-pages/attempt-1/`. An earlier typecheck run
  failed on a test literal (`finished_at` type in RunDetail.test.tsx) and was fixed before
  the final run.
- Screenshot smoke for these pages is owned by v12-p12.


### Session 9 repair P09-ADV-M01 (step7 comm-000368, attempt-2)

- Defect: the template text resource used a fresh `[name, version]` tuple as its source, so
  every detail refetch (after Save or Activate) reloaded the text and the editor reset,
  clearing "Saved as version N (inactive)" and reverting the draft. Reproduced first with
  new assertions (2 failures, `attempt-2/repro-before-fix.log`).
- Fix (`TemplateDetail.tsx`): the text key is a memo compared by value (name and version),
  so an unchanged selection does not refetch. `activate()` pins the displayed version
  before the refetch, so unsaved edits survive. The open version resets when the route name
  changes, and a detail that belongs to another name is ignored. `onSaved` stays `refetch()`.
- Tests (`TemplateDetail.test.tsx`, assertions added only): the save test checks the notice,
  Body text and a single `versions/1` GET after the refetch settles; the activate test
  checks that typed Body text survives; new tests cover opening version 0 and navigating
  between two template names.
- Verification: install, typecheck, vitest and build exit 0; logs copied under
  `tmp/telescreen-v12-20261011-d3/v12-p09-ui-pages/attempt-2/`.
