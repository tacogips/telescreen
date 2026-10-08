# P14 UI Police, Collectors, Escalation, Audit, Assistant and Settings Pages Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-api-and-ui.md (UI pages, Assistant), design-docs/specs/design-map-reduce-sweeps.md (API and UI)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p14-ui-ops`
- planPath: `impl-plans/active/p14-ui-ops.md`
- wave: 3
- dependsOn: `p07-web-shell`
- writePaths:
  - `impl-plans/active/p14-ui-ops.md`
  - `src/web/src/pages/police`
  - `src/web/src/pages/collectors`
  - `src/web/src/pages/escalation`
  - `src/web/src/pages/audit`
  - `src/web/src/pages/chat`
  - `src/web/src/pages/settings`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 7, 8).

## Intent and Context

These are the operations pages required by the acceptance criteria: police, collectors
(the "spies" page under plain naming), audit search, and the assistant chat (the "Big
Brother chat" under plain naming), plus escalation policies and settings (where the
bearer token is entered). Use only the p07 API client, types and components.

## Non-goals

- No backend changes; no new npm packages; no OIDC.

## Deliverables

- `pages/police/index.tsx`: a workers table (`policeApi.workers`) with name, mode, labels,
  task types, status and heartbeat age (`RelativeTime`). Admins get a "Disable" button
  (`policeApi.disable`).
- `pages/collectors/index.tsx`:
  - A configs table: name, kind, trigger mode, cron, sweep cron, enabled.
  - Actions "Run now" (`collectorsApi.run`) and "Sweep now" (`collectorsApi.sweep`; 409
    shows "A sweep is already running").
  - The latest sweep per config from `mapJobsApi.list({collector_config_id})`, with a
    progress bar.
  - A create/edit form: kind select, trigger mode, cron, sweep cron, credential ref
    (`env:`/`file:` text, never a secret value) and `selector_json` in a `JsonTextArea`.
- `pages/escalation/index.tsx`: a list of policies. The editor shows the tiers as JSON in
  a `JsonTextArea` and saves through `escalationApi` (PUT by name, POST for new).
  Validation errors show the paths.
- `pages/audit/index.tsx`:
  - Search form fields: `q`, action, actor, target kind, from, to.
  - Results show seq, time, actor, action, target and masked summary.
  - A "Verify chain" button calling `auditApi.verify` shows "Chain intact" or "Broken at
    seq N".
- `pages/chat/index.tsx`: the Assistant chat. It keeps the transcript in component state
  only (the server stores none) and sends the whole transcript to `chatApi.send`. It
  renders the answer (markdown shown as preformatted text; no HTML injection) and a
  "Clear" button.
- `pages/settings/index.tsx`: a token input saved with `setToken` (it is not shown again
  after saving: display "Token set"), a "Clear token" button, and the environment label.
- Tests (`*.test.tsx`, mocked fetch):
  - police renders the heartbeat age and disable calls the right URL;
  - a collectors sweep with a 409 shows the message;
  - the collectors form sends `selector_json` as a string;
  - audit verify renders the broken seq;
  - chat sends the full transcript on the second message and renders the answer as
    text (a `<b>` in the answer is shown literally);
  - settings stores the token in `sessionStorage` and hides it.

## Key Pitfalls

- Never use `innerHTML` for the chat or audit content.
- Never put the token in the URL or in logs; only `sessionStorage` and the header (p07
  client).
- Credential fields accept secret refs only. Validate the `^(env|file):` prefix
  client-side and show a hint.

## Verification

```bash
set -o pipefail
test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)   # p01 installed it; do not reinstall concurrently
cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p14-ui-ops-typecheck.log   # own files: zero errors
cd src/web && bunx vitest run src/pages/police src/pages/collectors src/pages/escalation src/pages/audit src/pages/chat src/pages/settings 2>&1 | tee /tmp/telescreen-p14-ui-ops-test.log   # exit 0
cd src/web && bun run build --outDir /tmp/telescreen-p14-ui-ops-web --emptyOutDir   # exit 0
```

## Completion Criteria

- [ ] Six pages delivered with the behaviors above
- [ ] Tests listed above pass
- [ ] No edits outside the six page directories
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
