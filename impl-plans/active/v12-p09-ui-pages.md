# V12-P09 UI Pages: Settings (General, Templates, Users and Roles), Workflow Sets, Run Targets

**Status**: Not Started
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
  invalid YAML shows the fallback text.
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

- [ ] Deliverables 1-6 implemented.
- [ ] typecheck, vitest and build exit 0.
- [ ] No files changed outside the three page directories (`git status --porcelain`).
- [ ] Progress Log complete.

## Progress Log
