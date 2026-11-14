# P14 UI Police, Collectors, Escalation, Audit, Assistant and Settings Pages Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-ui-sentry.md sections 6 (Police, Collectors, Escalation, Audit, Assistant, Settings), 7, 8.1, 8.1a, 8.2; design-docs/specs/design-map-reduce-sweeps.md (API and UI)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09 (run B, session 341)

## Plan Metadata

- planId: `p14-ui-ops`
- planPath: `impl-plans/active/p14-ui-ops.md`
- wave: B2 (parallel with p12, p13, p28)
- dependsOn: `p30-ui-design-system`, `p31-ui-mock-api`
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

Read `impl-plans/conventions.md` first (sections 2, 7, 7a, 8).

## Run B Intent

Run B rebuilds these six pages on the design system (design-ui-sentry.md section 6
constrained by 8.1). Wave B1 delivered `components/ui`, `TopBar.Slot`
(`components/shell/TopBar.tsx`), the theme helpers `getTheme`/`setTheme`
(`components/shell/theme.ts`, key `telescreen.theme`) from p30, and the aligned API
types plus mock API from p31 (read `src/web/src/api/types.ts` and p31's Progress Log for
the mechanical edits already made here). The Assistant area path is now `/assistant`
(page directory stays `pages/chat`). Keep the `*Api` calls and the behaviors covered by
existing tests; change markup and add the patterns below.

Non-goals (run B): no edits outside the six page directories and this plan; no npm or
backend changes; omitted per design 8.1: police in-flight count, contract version,
heartbeat timeline and recent tasks; collector last scan, findings in period, errors;
escalation "used by workflows" and history; API token listing; tool-call trace in chat;
docked assistant panel.

## Run B Deliverables (supersede the run A list below where they differ)

- Every page: TopBar title through `TopBar.Slot name="title"` (no `<h1>`), actions
  through `name="actions"`, `Table`/`Panel` layouts, `EmptyState`, `Placeholder` while
  loading, `ErrorBanner` on errors, `showToast` after mutations.
- Police (`pages/police/index.tsx`): `Table` columns name, mode, status dot, last
  heartbeat (`RelativeTime`), task types, labels, version. Status dot rule:
  `disabled` -> muted "Disabled"; `active` and heartbeat within 5 minutes -> success
  "Healthy"; otherwise warning "Stale" (text always visible). Row click opens a `Drawer`
  with all worker fields and an admin Disable button (`{id, status}` response -> reload).
- Collectors (`pages/collectors/index.tsx`): one row per config: lucide kind icon, name,
  trigger mode, cron, sweep cron, enabled `Tag`, latest sweep status (one
  `mapJobsApi.list()` call; latest item per `collector_config_id` by `created_at`);
  actions Run now (`{id}`) and Sweep now (`{map_job_id}`; 409 -> "A sweep is already
  running"); a Drawer with `mapJobsApi.get(id)` progress (absent counts are 0); create/
  edit form in a Drawer sending the full collector body (nullable fields as `null`;
  credential ref must match `^(env|file):`); enable/disable sends the full body built
  from the loaded config; empty state "Connect a source".
- Escalation (`pages/escalation/index.tsx`): list name and tier count (parse
  `tiers_json` defensively; unparsable -> "-"); detail shows tiers as read-only blocks
  (after, recipients) plus a mono JSON source editor; Save sends `{tiers_json,
  source_format: "json"}` (POST new, PUT `/{name}` existing); validation error paths
  listed.
- Audit (`pages/audit/index.tsx`): filters q, action, actor, target kind, from, to;
  `Table` actor (kind + ref), action, target, masked summary (text), time; offset
  `Pagination` from `{items, total}` (limit 50); chain banner from `auditApi.verify()`:
  "Chain verified up to #<checked>" (success) or "Chain broken at #<first_broken_seq>"
  (danger). Null fields render "-".
- Assistant (`pages/chat/index.tsx`): right-aligned user bubbles (max 80%), full-width
  assistant messages as text (no HTML); reads `?context=` (chip "Context: <path>",
  prefixed to the first message as `Context: <path>`) and `?q=` (prefills the input);
  Clear button; transcript only in component state.
- Settings (`pages/settings/index.tsx`): sections Account (token entry kept from run A;
  theme `Select` System / Light / Dark via `getTheme`/`setTheme`), Members and teams
  (`directoryApi.members()`, `teams()` showing slug, name, email, Slack channel; admin
  forms create member, create team, set team members by handles via
  `setTeamMembers(slug, handles)`), API tokens (text: "Create tokens with `telescreen
  token create --label <label> --scopes read,write`"), Collector credentials (each
  config's `credential_ref`, refs only), About (`healthApi.get()` status).

## Run B Tests (add; keep existing tests green with serializer-shaped responses)

- Police: stale heartbeat (10 minutes old) shows "Stale"; disabled shows "Disabled";
  Disable calls `/api/police/workers/<id>/disable` and reloads.
- Collectors: Sweep now 409 shows the message; enable toggle sends the full body with
  `enabled:false`; latest sweep chosen by `created_at` from one map-jobs request.
- Escalation: Save sends `{tiers_json, source_format:"json"}` via PUT for an existing
  name.
- Audit: `{items, total: 120}` renders pagination; next page requests `offset=50`;
  broken chain banner shows the seq.
- Assistant: `?context=/issues/iss-01` shows the chip and the first request's first
  message starts with `Context: /issues/iss-01`; `<b>` in an answer renders literally.
- Settings: theme select calls `setTheme("dark")` (assert `data-theme="dark"`); team
  members form sends `{handles:[...]}`; token stays hidden after save.

## Run A Intent and Context (history)

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
test -d src/web/node_modules || (cd src/web && bun install --frozen-lockfile)
cd src/web && bun run typecheck 2>&1 | tee /tmp/telescreen-p14-ui-ops-typecheck-final.log; echo "exit=$?"
cd src/web && bunx vitest run src/pages/police src/pages/collectors src/pages/escalation src/pages/audit src/pages/chat src/pages/settings 2>&1 | tee /tmp/telescreen-p14-ui-ops-test-final2.log; echo "exit=$?"
cd src/web && bun run build --outDir /tmp/telescreen-p14-ui-ops-web-final --emptyOutDir 2>&1 | tee /tmp/telescreen-p14-ui-ops-build-final.log; echo "exit=$?"
! grep -rn 'innerHTML\|<h1' src/web/src/pages/police src/web/src/pages/collectors src/web/src/pages/escalation src/web/src/pages/audit src/web/src/pages/chat src/web/src/pages/settings
test -z "$(find src/web/src/pages/police src/web/src/pages/collectors src/web/src/pages/escalation src/web/src/pages/audit src/web/src/pages/chat src/web/src/pages/settings -name '*.ts*' -exec wc -l {} + | awk '$1 >= 400 && $2 != "total"')"
```

All exit 0. If `typecheck` fails only in p12/p13 page files still in progress, record
the log and re-run later; your paths must show zero errors. The screenshot smoke is run
by p29.

## Completion Criteria

- [x] Six pages rebuilt per Run B Deliverables
- [x] Run B tests and the retained run A tests pass; verification commands pass with logs recorded
- [x] Reactive ErrorBanner rendering is covered by real-envelope failure tests on police, collectors, audit, Assistant, and settings (P14-TI-03)
- [x] No source edits outside the six page directories and this plan
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

### Session: 2026-10-09 17:30 JST
**Tasks Completed**: Rebuilt all six operations pages on the accepted design system and API contracts. Police health/details/disable, collector create/edit/enable/run/sweep and newest sweep progress, escalation tier summaries and JSON save, audit filters/chain verification/offset pagination, contextual assistant chat, and settings for tokens/themes/members/teams/credentials/about are implemented. Added run B behavioral coverage while retaining the existing page behaviors. Resource-load failures now render safe error banners.
**Tasks In Progress**: None within p14. Formal test-integrity, adversarial review and serial integration review are downstream workflow steps.
**Blockers**: None for assigned p14 scope.
**Reconciliation requests**: None.
**Hashes**: plan before=245a5e8a6f39d51fdd90cee87ac005703916df4fc8e02f827203566bf33b9039; plan after is recorded in `tmp/telescreen-v1-20261009-s341/p14-ui-ops/step6/plan-after-sha256.txt`. police/index.tsx before=a8fee5f2096c4729d0134c6ee534723af8bf1f744d99cec5c648cd2831704b28 after=346c6e4e06c0618d0a35735adcb754adae7294146164a8939ac558f7a18165c8; police/index.test.tsx before=6e01453f08d7e5213577ec88281d4eaabc83597b64b33a78552d4966c82477b3 after=861cff289986b40d05e19dc28ab4314605af89274b1d565c58dced2339a8cf3c; collectors/index.tsx before=fa3a5d8ac603b3a1792fb2f768168dc0c27862c79607068edc4e6c0286ffba5a after=01b7be6890648a78a1517d840f65c4e4d3c9c85cfe236098ad80505b0072b6d4; collectors/index.test.tsx before=5ee5356141945ba24e9f4ba51c6fbbdfded59b5990ceea48675fa99307d4cac3 after=0431d9786659a31d4e889291e898cb8dc4bc850a8dbcef4573d1526e37814574; escalation/index.tsx before=289880ec74430aa424a400e7b491faae4e4a5bad6d6512bf280494c56d953a32 after=4d8d17777ca537c682a46aee71da3334661bf104b9c8258529769bc3c6ee9cc8; escalation/index.test.tsx before=fc6e8d8750e77ba4f3a26e5859e6a14717fec71fa1c5a3fdd099ce95383b1411 after=6fa982952fe89d9fce41b63b544fe9e05f8d9db0a179ca512885bd39a20dc106; audit/index.tsx before=face0ba5643eb711ec3f90c6d56eb1ab2363bbd5b66cc8c325292f21c152381f after=250aa952e5ce4625e220848169fd68373677bfeee5bbdcf98bb1f45b06c39870; audit/index.test.tsx before=cb626d24e4af7e58a2a91189a46256f4eabc783fc93c7ec177b7412993d60be9 after=b30c878d0fe924838c132ddfdc306bd1d269763bf10adc3b28d4e9c12013e82f; chat/index.tsx before=da9edb6394815af0cfc929660fc6da24e054f80ccecc678dd5cbaea59992e0a0 after=04d39f3ed9f79b965eda75bc169c3abf53fd7a4b45e1e19b086253dde62c711a; chat/index.test.tsx before=1957cf02a0cb68f42bbeb9f15dd1956162a070abd41a650ae753d28f9921f999 after=4b658778b18b2e20bd64c6d6ac32dd68128b5fd74dcf49dba7ad77447db03f28; settings/index.tsx before=5defbdb82094fb5d6600102eefe1c11ea06695bb52010a9a2d9aeaf37b97a08f after=e5890c2b6378561d74e072a87b39bbc92dc61e796fd3e386c49ef3c387ac4db0; settings/index.test.tsx before=712d17950cb9bd32fabbc329afe525c40ea19ecf4eb4f66529cf1ef1963ebda1 after=57473b0820211abf23c3cc5890936c45fce9d7f629c7d08a9b3a76405d9667cd.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run typecheck` -> exit 0 (log `/tmp/telescreen-p14-ui-ops-typecheck-final2.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bunx vitest run src/pages/police src/pages/collectors src/pages/escalation src/pages/audit src/pages/chat src/pages/settings` -> exit 0, 14/14 tests (log `/tmp/telescreen-p14-ui-ops-test-final3.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; cd src/web && bun run build --outDir /tmp/telescreen-p14-ui-ops-web-final2 --emptyOutDir` -> exit 0 (log `/tmp/telescreen-p14-ui-ops-build-final2.log`); p14-only no-`<h1>`/`innerHTML` check and 400-line limit -> exit 0 (logs `/tmp/telescreen-p14-ui-ops-markup-check.log`, `/tmp/telescreen-p14-ui-ops-line-limit.log`).
**Notes**: An earlier targeted run failed two assertions before fixes; final p14 rerun passes. Initial typecheck reported concurrent issue/search page errors; the final current-tree typecheck passes. An additional full `bun run test` aggregate returned exit 1 with 89 passed, 7 failed across `src/pages/issues/IssueList.test.tsx` and `src/pages/workflows/WorkflowEditor.test.tsx` (log `/tmp/telescreen-p14-ui-ops-test-all-final.log`); those paths are outside p14 ownership and remain for their owning page plans/serial integration. The collector list response has no server sort option, so the client chooses the newest `created_at` from one list request.

### Session: 2026-10-09 P14-TI-01 repair
**Tasks Completed**: Restored exact request-body assertions for escalation policy creation and update. The POST test parses the `/api/escalation-policies` call with method POST and asserts `name`, edited `tiers_json`, and `source_format`; the PUT test asserts the edited tier payload and JSON format.
**Tasks In Progress**: Independent test-integrity/adversarial review and serial combined-tree integration remain downstream.
**Blockers**: None within p14 scope.
**Hashes**: `src/web/src/pages/escalation/index.test.tsx` before=6fa982952fe89d9fce41b63b544fe9e05f8d9db0a179ca512885bd39a20dc106 after=8967fff4e35ba036335cd8095895f8eec5314e74d35bcd13feeee617fccca58a. This repair addresses finding P14-TI-01.
**Verification**: `set -o pipefail; cd src/web; bunx vitest run src/pages/police src/pages/collectors src/pages/escalation src/pages/audit src/pages/chat src/pages/settings` -> exit 0, 6 files and 14/14 tests passed (log `/tmp/telescreen-p14-ui-ops-test-ti1-rerun.log`); `set -o pipefail; cd src/web; bun run typecheck` -> exit 0 (log `/tmp/telescreen-p14-ui-ops-typecheck-ti1.log`). The no-`<h1>`/`innerHTML` and line-limit gates remain covered by the passing current-source logs above. The first TI-01 test attempt exited 1 with 13 passed/1 failed because the lookup selected the initial GET call sharing the POST URL (log `/tmp/telescreen-p14-ui-ops-test-ti1.log`); the lookup was constrained to method POST and the full suite then passed.

### Session: 2026-10-09 P14-ADV-01 repair
**Tasks Completed**: Rebuilt escalation policy parsing, tier display, default document, and save validation around the backend DSL document `{name, tiers:[{after, assign_to, notify, channels}]}`. New and edited saves preserve the document name shown in the UI; syntactically valid JSON reaches the server for authoritative validation. Retained exact P14-TI-01 POST/PUT body equality and added real-shape list, create, update, invalid JSON, server validation path, and malformed stored document coverage.
**Tasks In Progress**: Independent adversarial re-review and serial integration reconciliation remain downstream.
**Blockers**: None within p14 write paths.
**Reconciliation requests**: `src/web/src/api/mock/fixtures/operations.ts` remains p31-owned and still supplies the invented `{after_seconds, recipients}` shape. Do not edit it in p14; serial integration must amend p31 scope or assign a fixture repair before mock escalation can render valid policies.
**Hashes**: `src/web/src/pages/escalation/index.tsx` before=4d8d17777ca537c682a46aee71da3334661bf104b9c8258529769bc3c6ee9cc8 after=92d0b3a79afcf20208b041120bf9f7d432744f795655c4cecaa9cba513f73f78; `src/web/src/pages/escalation/index.test.tsx` before=8967fff4e35ba036335cd8095895f8eec5314e74d35bcd13feeee617fccca58a after=5ef6d43f98249bafe7eb8fc491487fba11adaa1ab0b3e1db50da3f2948cc0de5. This repair addresses P14-ADV-01 and preserves P14-TI-01 exact POST/PUT assertions.
**Verification**: `set -o pipefail; cd src/web; bunx vitest run src/pages/police src/pages/collectors src/pages/escalation src/pages/audit src/pages/chat src/pages/settings` -> exit 0, 6 files and 17/17 tests passed (`/tmp/telescreen-p14-adv1-test.log`); `set -o pipefail; cd src/web; bun run typecheck` -> exit 0 (`/tmp/telescreen-p14-adv1-typecheck.log`); `set -o pipefail; cd src/web; bun run build --outDir /tmp/telescreen-p14-adv1-web --emptyOutDir` -> exit 0 (`/tmp/telescreen-p14-adv1-build.log`). Markup, legacy-shape, and direct backend DSL loader checks exited 0 (`/tmp/telescreen-p14-adv1-markup.log`, `/tmp/telescreen-p14-adv1-legacy-shape.log`, `/tmp/telescreen-p14-adv1-backend-contract.log`).

### Session: 2026-10-09 P14-TI-02 repair
**Tasks Completed**: Restored local escalation DSL validation before saving parseable documents. Validation reports backend-equivalent paths/codes for invalid duration, assignment, notify, channel and tier structure without sending a request. Replaced the fictitious escalation 422 mock with local-path coverage and the actual 500 `workflow_invalid` envelope; the error banner is reactively rendered, the drawer stays open, and no success toast appears. Preserved the exact P14-TI-01 POST/PUT body assertions and all other escalation tests.
**Tasks In Progress**: Independent test-integrity/adversarial re-review and serial integration remain downstream.
**Blockers**: None within p14 write paths.
**Reconciliation requests**: Backend follow-up for serial integration/backend ownership: `src/telescreen/api/routers/escalation.py` lets `WorkflowValidationError` reach the generic handler, which returns HTTP 500 `{error:{code:"workflow_invalid",detail:"workflow definition is invalid"}}` without validation paths. A backend owner should decide whether the escalation router should return the workflows-style 422 `{errors:[{path,code}]}` contract. No backend or p31-owned files were changed.
**Hashes**: `src/web/src/pages/escalation/index.tsx` before=92d0b3a79afcf20208b041120bf9f7d432744f795655c4cecaa9cba513f73f78 after=dee4987bdde4b9bccde151b99cbd3f874719864269d47930812ebb2e12acf790; `src/web/src/pages/escalation/index.test.tsx` before=5ef6d43f98249bafe7eb8fc491487fba11adaa1ab0b3e1db50da3f2948cc0de5 after=2d926072d0c4f1972bd4ffc1dba19255cea6992a0d8b3067cc9d25ef72fc10ff. This repair addresses P14-TI-02 and retains P14-TI-01 exact payload coverage.
**Verification**: `cd src/web && bunx vitest run src/pages/police src/pages/collectors src/pages/escalation src/pages/audit src/pages/chat src/pages/settings` -> exit 0, 6 files and 18/18 tests (`/tmp/telescreen-p14-ti2-test.log`); `cd src/web && bun run typecheck` -> exit 0 (`/tmp/telescreen-p14-ti2-typecheck.log`); `cd src/web && bun run build --outDir /tmp/telescreen-p14-ti2-web --emptyOutDir` -> exit 0 (`/tmp/telescreen-p14-ti2-build.log`); no fictitious 422 mock, no `<h1>`/`innerHTML`, page under 400 lines, zero unchecked plan criteria, and `git diff --check` -> exit 0 (`/tmp/telescreen-p14-ti2-static.log`). The first full test attempt reported 16/18 passing and two assertion failures; focused reruns exposed the split-text matcher and nonreactive inline error banner. The matching fixes were applied and the final full suite and focused escalation suite pass (`/tmp/telescreen-p14-ti2-escalation-rerun4.log`, 7/7). Earlier focused failure logs: `/tmp/telescreen-p14-ti2-escalation-rerun1.log`, `/tmp/telescreen-p14-ti2-escalation-rerun2.log`, `/tmp/telescreen-p14-ti2-escalation-rerun3.log`. The initial full-suite logfile path was reused by the final passing run, so its terminal-reported 16/18 failure details are preserved here rather than in a separate log file.

### Session: 2026-10-09 P14-TI-03 repair
**Tasks Completed**: Wrapped the five remaining P14 error banners (police, collectors, audit, Assistant, settings) in reactive Solid `Show` callbacks, preserving each page's original error/resource expression. Added one API failure test per page using the real HTTP 500 `{error:{code:"internal_error",detail:"failed"}}` envelope and asserting `role="alert"` contains `internal_error`. The existing collectors 409 notice and all other p14/escalation assertions remain intact.
**Tasks In Progress**: Independent test-integrity/adversarial re-review and serial combined-tree integration remain downstream.
**Blockers**: None within p14 write paths.
**Reconciliation requests**: `src/web/src/components/ErrorBanner.tsx` is p30/p07-owned and remains non-reactive at its source; p30 or serial integration should repair the component so pages outside p14 also update after asynchronous errors. No component or API files were changed.
**Hashes**: plan before=`a8d1bf8906265daca2a86c537084db82c3d8cf327e9a12c81a79a80880a9349c`; police/index.tsx before=`346c6e4e06c0618d0a35735adcb754adae7294146164a8939ac558f7a18165c8` after=`e8c105fbfc69861f926a006949e4bb646cb17d9649ad786644dd7bbde543eb5c`; police/index.test.tsx before=`861cff289986b40d05e19dc28ab4314605af89274b1d565c58dced2339a8cf3c` after=`82fed47c69182f45d18d2cba21e0bcea7efd1aa74ee3a9ebca1f5eee7349ed25`; collectors/index.tsx before=`01b7be6890648a78a1517d840f65c4e4d3c9c85cfe236098ad80505b0072b6d4` after=`0b47b9ab2fd0656cfb2d4f49099ca11499a76aabe48ca39ee2ff96ca5aae4270`; collectors/index.test.tsx before=`0431d9786659a31d4e889291e898cb8dc4bc850a8dbcef4573d1526e37814574` after=`28667d7dfe56ed113e9063a0de2247eaf5b2203e198abd1fb0416ba8b809bd80`; audit/index.tsx before=`250aa952e5ce4625e220848169fd68373677bfeee5bbdcf98bb1f45b06c39870` after=`8637399ea9860c9d9d7808fbefb852df86c83f929f5d93eae66ff935c7e63ae5`; audit/index.test.tsx before=`b30c878d0fe924838c132ddfdc306bd1d269763bf10adc3b28d4e9c12013e82f` after=`f158743e10ca3d0b1e3220d51acebe163719f5cf3035d233266dc1230f76a77c`; chat/index.tsx before=`04d39f3ed9f79b965eda75bc169c3abf53fd7a4b45e1e19b086253dde62c711a` after=`465a00d77a96ad181e78de63f083c2c73c8b91a77cb23ab5f5eafc24300fdb57`; chat/index.test.tsx before=`4b658778b18b2e20bd64c6d6ac32dd68128b5fd74dcf49dba7ad77447db03f28` after=`850434afb5f0744fe0f9a159246b50073e9038b811a220373d8b95f939d48cbe`; settings/index.tsx before=`e5890c2b6378561d74e072a87b39bbc92dc61e796fd3e386c49ef3c387ac4db0` after=`ad157ba038e1e9b08ad046dbac517b2186515ace1f29f0ef8c1a57c114d05045`; settings/index.test.tsx before=`57473b0820211abf23c3cc5890936c45fce9d7f629c7d08a9b3a76405d9667cd` after=`181055a49f3823bb7930c4fa778c496ffcdd4bd71f7330947f3364888ef9ff38`.
**Verification**: `cd src/web && bunx vitest run src/pages/police src/pages/collectors src/pages/escalation src/pages/audit src/pages/chat src/pages/settings` -> exit 0, 6 files and 23/23 tests (`/tmp/telescreen-p14-ti3-test-rerun.log`); the first run had 21/23 pass and two incorrect URL/method expectations, corrected to the actual API client paths (`/tmp/telescreen-p14-ti3-test.log`). `cd src/web && bun run typecheck` -> exit 0 (`/tmp/telescreen-p14-ti3-typecheck.log`). `cd src/web && bun run build --outDir /tmp/telescreen-p14-ti3-web --emptyOutDir` -> exit 0 (`/tmp/telescreen-p14-ti3-build.log`). The direct ErrorBanner grep, `<h1>`/`innerHTML` check, 400-line limit, zero-unchecked criteria check and `git diff --check` -> exit 0 (`/tmp/telescreen-p14-ti3-static-final.log`).
