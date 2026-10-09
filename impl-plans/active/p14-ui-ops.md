# P14 UI Police, Collectors, Escalation, Audit, Assistant and Settings Pages Implementation Plan

**Status**: Completed
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
cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p14-ui-ops-typecheck-final.log   # exit 0
cd src/web && bunx vitest run src/pages/police src/pages/collectors src/pages/escalation src/pages/audit src/pages/chat src/pages/settings 2>&1 | tee /tmp/telescreen-p14-ui-ops-test-final.log   # 8 passed
cd src/web && bun run build --outDir /tmp/telescreen-p14-ui-ops-web --emptyOutDir 2>&1 | tee /tmp/telescreen-p14-ui-ops-build.log   # exit 0
```

## Completion Criteria

- [x] Six pages delivered with the behaviors above
- [x] Tests listed above pass
- [x] No source edits outside the six page directories
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 22:21 JST
**Tasks Completed**: Implemented police, collectors, escalation, audit, assistant chat and settings pages with colocated tests; updated completion criteria and verification paths.
**Tasks In Progress**: Formal test-integrity/adversarial and serial integration reviews remain downstream.
**Blockers**: None
**Reconciliation requests**: None. p07 typed collector helpers omit sweep_cron and credential_ref; page uses the existing apiFetch helper for those endpoint fields without changing shared API files.
**Hashes**: police/index.tsx=a8fee5f2096c4729d0134c6ee534723af8bf1f744d99cec5c648cd2831704b28; police/index.test.tsx=0a8a7a733fb5c6b6868a11c933d14b157ca3789761d4921c6b59bf9fbf656629; collectors/index.tsx=48cac4d2ebb0cb778f135c7fb5f5ee7364b75d07b2053f0fe02671616ddcf16b; collectors/index.test.tsx=5ee5356141945ba24e9f4ba51c6fbbdfded59b5990ceea48675fa99307d4cac3; escalation/index.tsx=8b42f2adde3ac86e8fa5196ef955caf6e621760619ef8667be832b313ef5d003; escalation/index.test.tsx=d58e95e4d28e5bdef5242e297d3e731852afcf1ed1c4f8aa9008a11f338f2392; audit/index.tsx=face0ba5643eb711ec3f90c6d56eb1ab2363bbd5b66cc8c325292f21c152381f; audit/index.test.tsx=cb626d24e4af7e58a2a91189a46256f4eabc783fc93c7ec177b7412993d60be9; chat/index.tsx=da9edb6394815af0cfc929660fc6da24e054f80ccecc678dd5cbaea59992e0a0; chat/index.test.tsx=1957cf02a0cb68f42bbeb9f15dd1956162a070abd41a650ae753d28f9921f999; settings/index.tsx=5defbdb82094fb5d6600102eefe1c11ea06695bb52010a9a2d9aeaf37b97a08f; settings/index.test.tsx=712d17950cb9bd32fabbc329afe525c40ea19ecf4eb4f66529cf1ef1963ebda1.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck` -> exit 0 (log: /tmp/telescreen-p14-ui-ops-typecheck-final.log); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/police src/pages/collectors src/pages/escalation src/pages/audit src/pages/chat src/pages/settings` -> exit 0, 8 passed (log: /tmp/telescreen-p14-ui-ops-test-final.log); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-p14-ui-ops-web --emptyOutDir` -> exit 0 (log: /tmp/telescreen-p14-ui-ops-build.log).
**Notes**: Earlier test attempt failed 2/8 due test setup and is superseded by the passing current-source rerun at /tmp/telescreen-p14-ui-ops-test-final.log. Earlier typecheck found two errors in concurrently added `src/web/src/pages/runs/RunDetail.tsx`; final typecheck of the current shared tree exits 0. The map-jobs client has no timestamp/sort contract, so “latest” currently uses API list order.

### Session: 2026-10-09 07:30 JST
**Tasks Completed**: Re-verified the six p14 pages and their colocated tests on the current shared tree; no page source changes were needed.
**Tasks In Progress**: Downstream formal reviews and serial integration remain owned by later workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: plan before=ea10d6db66608f4fb7a45b4c781c631536bd7976702b3001b442e895c49f0392 after=8a5abf1177275f9a4766bd6ec16948e2454052e9881a833aa9620393e4204452; police/index.tsx=a8fee5f2096c4729d0134c6ee534723af8bf1f744d99cec5c648cd2831704b28; police/index.test.tsx=0a8a7a733fb5c6b6868a11c933d14b157ca3789761d4921c6b59bf9fbf656629; collectors/index.tsx=48cac4d2ebb0cb778f135c7fb5f5ee7364b75d07b2053f0fe02671616ddcf16b; collectors/index.test.tsx=5ee5356141945ba24e9f4ba51c6fbbdfded59b5990ceea48675fa99307d4cac3; escalation/index.tsx=8b42f2adde3ac86e8fa5196ef955caf6e621760619ef8667be832b313ef5d003; escalation/index.test.tsx=d58e95e4d28e5bdef5242e297d3e731852afcf1ed1c4f8aa9008a11f338f2392; audit/index.tsx=face0ba5643eb711ec3f90c6d56eb1ab2363bbd5b66cc8c325292f21c152381f; audit/index.test.tsx=cb626d24e4af7e58a2a91189a46256f4eabc783fc93c7ec177b7412993d60be9; chat/index.tsx=da9edb6394815af0cfc929660fc6da24e054f80ccecc678dd5cbaea59992e0a0; chat/index.test.tsx=1957cf02a0cb68f42bbeb9f15dd1956162a070abd41a650ae753d28f9921f999; settings/index.tsx=5defbdb82094fb5d6600102eefe1c11ea06695bb52010a9a2d9aeaf37b97a08f; settings/index.test.tsx=712d17950cb9bd32fabbc329afe525c40ea19ecf4eb4f66529cf1ef1963ebda1.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck` -> exit 0 (log: /tmp/telescreen-p14-ui-ops-typecheck-verified.log); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/police src/pages/collectors src/pages/escalation src/pages/audit src/pages/chat src/pages/settings` -> exit 0, 8 passed (log: /tmp/telescreen-p14-ui-ops-test-verified.log); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-p14-ui-ops-web-verified --emptyOutDir` -> exit 0 (log: /tmp/telescreen-p14-ui-ops-build-verified.log).
**Notes**: The before:step6-implement snapshot is `tmp/riela-fanout/7270E8A4-6D86-4A79-971B-97A19A8FEA1E/01AB8845-AC7B-40F7-93A4-DF28D31B8A10.json`. Existing implementation and test files remain unchanged; only this progress entry was added. The collectors map-job list has no timestamp/sort contract, so “latest” uses API list order as previously recorded.
