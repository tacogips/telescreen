# V13-P17 Verification and Docs: v1.3 Leak Matrix, E2E Flows, Live Checks, Strict Screenshot Smoke, README and Status

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 7 (safety rules), 14, 15 ("Run 3" notes, wave C), 16, 17; design-docs/specs/design-v1-3-sentry-parity-review.md section 6; design-docs/specs/design-ui-sentry.md 11.3
**Created**: 2026-10-12
**Last Updated**: 2026-10-13 (run 3, manifest telescreen-v13-20261013-e4, wave C)

## Plan Metadata

- planId: `v13-p17-verification-docs`
- planPath: `impl-plans/active/v13-p17-verification-docs.md`
- wave: 5 (run 3 wave C; runs alone, after wave B = p12)
- dependsOn: `v13-p12-wiring`, `v13-p13-ui-stream`, `v13-p14-ui-detail`, `v13-p15-ui-alerts-profile`, `v13-p16-ui-dashboard-org`
- writePaths:
  - `impl-plans/active/v13-p17-verification-docs.md`
  - `tests/leak/flows_v13.py`
  - `tests/leak/test_leak_matrix_v13.py`
  - `tests/e2e/test_flow_v13_stream.py`
  - `tests/e2e/test_flow_v13_archive.py`
  - `tests/e2e/test_flow_v13_detail.py`
  - `tests/e2e/test_flow_v13_org.py`
  - `tests/e2e/test_flow_v13_weekly.py`
  - `tests/live/v13`
  - `src/web/scripts/screenshots.ts`
  - `README.md`
  - `impl-plans/README.md`
  - `design-docs/specs/design-v1-3-sentry-parity.md`
  - `design-docs/specs/design-v1-3-sentry-parity-review.md`
  - `design-docs/specs/design-ui-sentry.md`
  - `design-docs/specs/design-api-and-ui.md`
  - `design-docs/specs/design-data-model.md`
  - `design-docs/specs/architecture.md`
- sharedPaths: none
- artifactRoots: none
- read-only (must pass unchanged; a failure is a Reconciliation request naming the owning
  plan and file): `tests/leak/harness.py`, `tests/leak/sinks.py`, the v1/v11/v12 leak
  flows and matrices, `tests/leak/test_ui_fixtures.py`, `tests/db/test_models.py`,
  `tests/examples`, `tests/live/conftest.py` and the v1.2 live tests, `tests/e2e/harness.py`,
  all of `src/telescreen` (including p12's uncommitted wiring), `src/web/src`,
  `src/web/package.json`, `src/web/bun.lock`, `examples`, `pyproject.toml`, `uv.lock`,
  `tools/typelint.py`.

Read `impl-plans/conventions.md` first (sections 2, 7, 7a, 8).

## Intent and Context

Prove v1.3 end to end on the wired tree, and document it:

- the leak matrix extension (design 14, flows 1-9);
- E2E flows E1-E5 (design 16), including the merge-into-archived assertions;
- env-gated live checks (Keycloak, Mailpit, Slack test channel);
- the strict screenshot smoke over the realistic seed (`src/web/src/api/mock/fixtures/v13.ts`
  seeds 40 issues across statuses, plus the legacy fixtures);
- the README v1.3 features, the plan index and the design status lines.

Archiving the v13 plans and manifests is the serial finalization after this plan. This plan
does not move files.

Imitate:

- `tests/leak/flows_v12.py` and `test_leak_matrix_v12.py` (a `make_leak_world` per flow;
  assert all sinks from `tests/leak/sinks.py`: `database_text`, `logs`,
  `search_documents`, `outbound_bodies`, `api_responses`);
- `tests/e2e/test_flow_v12_notifications.py` and `tests/e2e/harness.py`;
- `tests/live/test_slack_delivery.py`, `test_mailpit_email.py`
  (`tests/live/conftest.py:require_live_variables`, marker `live`).

## Non-goals

- No feature code changes and no wiring changes. A defect becomes a Reconciliation request
  naming the owning plan and file; if it blocks acceptance, report it as a blocker.
- No edits to `tests/leak/harness.py`, `sinks.py` or v1/v11/v12 flows; no fixture edits in
  `src/web`; no new dependency.
- No plan or manifest moves (finalization).

## Invariants

- Sample secrets come only from `tests.support.samples` (assembled at runtime; gitleaks).
  Never write a literal sample secret in any file, doc or log line.
- Live tests are under `tests/live/v13/`, marked `live`, and call
  `require_live_variables` first. The offline `uv run pytest` passes with them skipped.
- Live variables (same names as v1.2): Slack `TELESCREEN_TEST_SLACK_BOT_TOKEN`,
  `TELESCREEN_TEST_SLACK_CHANNEL`; Mailpit `TELESCREEN_TEST_SMTP_HOST`,
  `TELESCREEN_TEST_SMTP_PORT`, `TELESCREEN_TEST_MAILPIT_URL`, `TELESCREEN_TEST_EMAIL_TO`;
  Keycloak `TELESCREEN_TEST_KEYCLOAK_URL` (existing test only).
- `screenshots.ts`: edit only when the strict run fails because a state targets an
  accessible name that differs from the accepted page; change only that state's selector
  and record the state, old and new name in the Progress Log. Never relax
  `TELESCREEN_SCREENSHOTS_STRICT`, remove a state or a theme.
- Docs carry no secret, token, host or real email.

## Deliverables

1. **`tests/leak/flows_v13.py`** and **`test_leak_matrix_v13.py`**: flows 1-9 of design 14
   exactly as listed (saved views; comment, mention, subscription, bookmark rows and the
   activity response; external link and GitHub body with DLP block; alert rule name and
   fire payloads; events selector, tags, stats, dashboard and weekly report; bulk resolve
   and archive, merge and unmerge; archive, escalation and expiry history and search
   documents; integration status and test, ownership test, member edit, self token;
   invalid query 422 body and logs). Each flow feeds the sample secrets into every
   user-controlled field it touches and asserts no sample appears in any sink. Use the
   real `build_container`, memory search, MockTransport Slack and GitHub, fake SMTP.
2. **E2E flows** (cases: situation -> expected):
   - `test_flow_v13_stream.py` (E1): seeded issues -> counts per tab; `!status:resolved`,
     `has:assignee`, `first_seen:-7d`, `times_seen:>5` (keys per design 5.1) -> exact
     ids; a shared view and a
     default view -> listed for another member and applied; bulk resolve by query ->
     processed equals matching; merge then unmerge -> child hidden then restored.
   - `test_flow_v13_archive.py` (E2): archive until 5 findings then 5 ingests ->
     `escalating`, an `issue_escalating` rule fires once, a second escalation inside the
     interval -> `suppressed_interval`; duration expiry through a scheduler tick with
     `FakeClock` -> `unresolved` and needs_review; merge into archived P
     (`until_findings` 5, child with 40 historical findings) -> P stays archived,
     `archive_until` unchanged, 4 findings keep it archived, the 5th escalates; unmerge ->
     the archived child escalates only after 5 of its own.
   - `test_flow_v13_detail.py` (E3): 120 findings -> oldest, next, latest and `total`
     120; tags from the server; a mention -> email to the mentioned member via fake SMTP
     according to their settings; subscriber activity -> excludes the actor.
   - `test_flow_v13_org.py` (E4): an ownership rule -> a new issue routes to its team;
     deactivation -> the member's open issues released; self token create and use.
   - `test_flow_v13_weekly.py` (E5): a weekly tick -> enqueues once (second tick same
     slot -> nothing); the handler -> one report per opted-in member.
3. **`tests/live/v13/`**:
   - `test_alert_live.py`: an alert rule (`issue_created`, `slack_channel:<env channel>`
     on slack and `email:<env to>` on email, interval 60) fires on a synthetic leak
     ingested through the push path -> one Slack message and one Mailpit mail; a second
     synthetic leak within the interval -> nothing new.
   - `test_mention_live.py`: a member with mentions=email and the Mailpit address is
     mentioned -> exactly one mail containing the short id and no sample value.
4. **`README.md`**: a "Features (v1.3)" section (stream tabs and query language, saved
   views, bulk and merge, archive and escalating, issue detail, alert rules, notification
   settings and weekly report, dashboard, organization pages, palette and shortcuts); the
   v1.3 API endpoints; the new UI pages; the new environment variables
   (`TELESCREEN_ESCALATING_*`, `TELESCREEN_WEEKLY_REPORT_CRON`,
   `TELESCREEN_GITHUB_ISSUES_*`, names from `src/telescreen/core/config.py`); the
   onboarding docs anchors the UI links to (grep `src/web/src/pages/dashboard` for the
   hrefs and match them exactly).
5. **Docs status**:
   - `impl-plans/README.md`: update the v1.3 run-3 line with the result (keep rows; the
     finalization moves them);
   - status lines of `design-v1-3-sentry-parity.md` and
     `design-v1-3-sentry-parity-review.md` -> "implemented 2026-10-13"; in
     `design-ui-sentry.md` 11/11.4, `design-api-and-ui.md` "v1.3 endpoints",
     `design-data-model.md` "v1.3 additions" and `architecture.md` section 4 "v1.3
     additions", change only wording that says planned or pending to implemented. Fix a
     factual mismatch with the code only when found, and record it.

## Pitfalls

- A leak test that only checks API responses is not enough: assert every sink.
- Fake SMTP and MockTransport must capture bodies, or the outbound sink is vacuous.
- Do not mark a live test as passed when skipped; the Progress Log says "not run" with the
  missing variable names.
- `FakeClock` must drive the scheduler tick; never `sleep`.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check . 2>&1 | tee /tmp/telescreen-v13-p17-format.log
uv run --frozen ruff check . 2>&1 | tee /tmp/telescreen-v13-p17-ruff.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p17-typelint.log 2>&1
uv run --frozen ty check 2>&1 | tee /tmp/telescreen-v13-p17-ty.log
uv run --frozen pytest -p no:cacheprovider -rs > /tmp/telescreen-v13-p17-pytest.log 2>&1
uv build --out-dir /tmp/telescreen-v13-p17-dist > /tmp/telescreen-v13-p17-build.log 2>&1
(cd src/web && bun install --frozen-lockfile) > /tmp/telescreen-v13-p17-install.log 2>&1
(cd src/web && bun run typecheck) > /tmp/telescreen-v13-p17-typecheck.log 2>&1
(cd src/web && bun run test) > /tmp/telescreen-v13-p17-vitest.log 2>&1
(cd src/web && bun run build --outDir /tmp/telescreen-v13-p17-web --emptyOutDir) > /tmp/telescreen-v13-p17-webbuild.log 2>&1
(cd src/web && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots) > /tmp/telescreen-v13-p17-shots.log 2>&1
! grep -n 'state-skipped' /tmp/telescreen-v13-p17-shots.log
grep -E 'tests/live/v13/.* SKIPPED|SKIPPED .*tests/live/v13' /tmp/telescreen-v13-p17-pytest.log
test -z "$(git diff --name-only HEAD -- src/telescreen | grep -v -E '^src/telescreen/(wiring[.]py|api/app[.]py)$')"
test -z "$(git diff --name-only HEAD -- tests/leak/harness.py tests/leak/sinks.py tests/leak/flows.py tests/leak/flows_v11.py tests/leak/flows_v12.py tests/db tests/examples tests/live/conftest.py src/web/src src/web/package.json src/web/bun.lock examples pyproject.toml uv.lock tools/typelint.py)"
test "$(grep -c '^- \[ \]' impl-plans/active/v13-p17-verification-docs.md)" -eq 0
```

Expected: every command exits 0. The pytest log shows the v13 leak and flow tests passed,
0 failed, and `tests/live/v13` skipped offline. The screenshot log has no `state-skipped`
line and no page or console error, and covers every state of design 16 in light and dark.
A missing browser is a recorded verification gap, never a pass.

Live dev-stack check (design 16; owner environment; not an offline pass gate):

```bash
uv run --frozen pytest -m live tests/live -p no:cacheprovider -rs > /tmp/telescreen-v13-p17-live.log 2>&1
```

Report per test: passed, failed, or "not run" with the missing variable names.

## Completion Criteria

- [x] Leak flows 1-9 and E1-E5 pass offline, including the safety rule 5 assertions.
- [x] Live tests exist under `tests/live/v13`, are marked `live` and skipped offline; the
  live dev-stack command result is recorded (passed or "not run" per test).
- [x] The strict screenshot smoke passes with no `state-skipped` line.
- [x] The README, plan index and design status lines are updated.
- [x] The Progress Log has per-edit pre/post SHA-256 and every command with its exit
  status and log path.

## Progress Log

(append sessions per conventions section 8)

### Session: 2026-10-13 final implementation verification
**Tasks Completed**: Added leak flows 1-9 and the matrix assertions across all five sinks; completed E1-E5, including archived-primary merge safety, expiry, ownership routing, mentions and weekly reports; added opt-in live alert and mention tests; updated README, plan index and six design status sections. Corrected fixture contracts found during focused runs (bulk resolve requires reason and note; mention handles cannot include trailing punctuation; mention email places `TELE-*` in its subject). Strict screenshots completed 89 outputs.
**Tasks In Progress**: Formal test-integrity/adversarial review and serial archive/commit/push are downstream workflow steps.
**Blockers**: None. `tests/live/v13/test_alert_live.py` not run; missing `TELESCREEN_TEST_SLACK_BOT_TOKEN`, `TELESCREEN_TEST_SLACK_CHANNEL`, `TELESCREEN_TEST_SMTP_HOST`, `TELESCREEN_TEST_SMTP_PORT`, `TELESCREEN_TEST_MAILPIT_URL`, `TELESCREEN_TEST_EMAIL_TO`. `tests/live/v13/test_mention_live.py` not run; missing `TELESCREEN_TEST_SMTP_HOST`, `TELESCREEN_TEST_SMTP_PORT`, `TELESCREEN_TEST_MAILPIT_URL`, `TELESCREEN_TEST_EMAIL_TO`.
**Reconciliation requests**: None.
**Hashes**: Per-edit intentions and intermediate pre-edit hashes are recorded in `tmp/telescreen-v13-20261013-e4/p17/edit-intents.md`, `edit-intents-e2e.md`, `edit-intents-live.md`, and `edit-intents-docs.md`. Final authored-file hashes are recorded in `tmp/telescreen-v13-20261013-e4/p17/final-hashes.txt`.
**Verification**:
- `uv run --frozen ruff format --check .` -> exit 0 (log: `/tmp/telescreen-v13-p17-format.log`).
- `uv run --frozen ruff check .` -> exit 0 (log: `/tmp/telescreen-v13-p17-ruff.log`).
- `uv run --frozen python tools/typelint.py` -> exit 0, 537 files clean (log: `/tmp/telescreen-v13-p17-typelint.log`).
- `uv run --frozen ty check` -> exit 0 (log: `/tmp/telescreen-v13-p17-ty.log`).
- `uv run --frozen pytest -p no:cacheprovider -rs` -> exit 0, 998 passed / 10 skipped / 0 failed (log: `/tmp/telescreen-v13-p17-pytest.log`).
- `uv build --out-dir /tmp/telescreen-v13-p17-dist` -> exit 0 (log: `/tmp/telescreen-v13-p17-build.log`).
- `cd src/web && bun install --frozen-lockfile` -> exit 0, no changes (log: `/tmp/telescreen-v13-p17-install.log`).
- `cd src/web && bun run typecheck` -> exit 0 (log: `/tmp/telescreen-v13-p17-typecheck.log`).
- `cd src/web && bun run test` -> exit 0, 235 passed (log: `/tmp/telescreen-v13-p17-vitest.log`).
- `cd src/web && bun run build --outDir /tmp/telescreen-v13-p17-web --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-v13-p17-webbuild.log`).
- `cd src/web && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots` -> exit 0; no `state-skipped` or browser-error line (log: `/tmp/telescreen-v13-p17-shots.log`).
- `uv run --frozen pytest -m live tests/live -p no:cacheprovider -rs` -> exit 0; v13 alert and mention checks not run for the variables listed above (log: `/tmp/telescreen-v13-p17-live.log`).
- Focused owned suite -> exit 0, 7 passed / 0 failed (log: `/tmp/telescreen-v13-p17-focused-final.log`).
**Notes**: Earlier focused attempts found test fixture/API mismatches; each was corrected and the focused suite plus final full suite passed on the current source. No screenshot script edit was needed. Review, archive, commit and push are not claimed complete here.

### Owner note: 2026-10-13 (session 14, live check run by the owner)
The agent environment has no live-service variables, so `pytest -m live tests/live` only skipped there; the progress gate treats an all-skipped command as a failure. The owner ran the live suite on the dev stack (Mailpit 127.0.0.1:1025/8025, Keycloak 127.0.0.1:8080, the Slack test channel given by `TELESCREEN_TEST_SLACK_CHANNEL`) and found two test-expectation bugs and one product bug, now fixed in the tree:
- tests/live/v13/test_alert_live.py expected exactly one alert_fires row after the repeated scan; per D21 a match inside the action interval records one `suppressed_interval` row and sends nothing. The test now asserts one `delivered` fire, only delivered/suppressed_interval outcomes, and still two notifications.
- tests/live/v13/test_mention_live.py used a member-bound token for a member without a role (a directory member has no role, so the bound token has no permissions, v1.2 design) and got 403; it now assigns the viewer role first.
- Product bug: a mentioned member received two emails for one comment (the mention, plus the `subscribed` activity notification because the mention auto-subscribes them). src/telescreen/issues/activity/notifier.py now skips members mentioned in the comment; tests/e2e/test_flow_v13_detail.py asserts the target gets exactly one `mentions` notification (fails without the fix).
With these fixes the live suite passes on the dev stack (5 passed). In this plan's step result, do NOT list the offline-skipped live command in `verification` or `priorVerification` (skipped live tests are not risks, conventions 7a); cite this owner note for the live evidence instead. Keep the fixes above.

### Session: 2026-10-13 05:25 Asia/Tokyo — P17-RETRY-01
**Tasks Completed**: Re-ran the P17 verification against the current tree; confirmed the live-test markers and offline skip status; refreshed the write-path hash inventory. Source, tests and docs outside this plan were not edited in this retry.
**Tasks In Progress**: Formal test-integrity/adversarial review and serial archive/commit/push are downstream workflow steps.
**Blockers**: None. `tests/live/v13/test_alert_live.py` not run in the agent environment; missing `TELESCREEN_TEST_SLACK_BOT_TOKEN`, `TELESCREEN_TEST_SLACK_CHANNEL`, `TELESCREEN_TEST_SMTP_HOST`, `TELESCREEN_TEST_SMTP_PORT`, `TELESCREEN_TEST_MAILPIT_URL`, `TELESCREEN_TEST_EMAIL_TO`. `tests/live/v13/test_mention_live.py` not run in the agent environment; missing `TELESCREEN_TEST_SMTP_HOST`, `TELESCREEN_TEST_SMTP_PORT`, `TELESCREEN_TEST_MAILPIT_URL`, `TELESCREEN_TEST_EMAIL_TO`. As directed by conventions 7a and the owner note below, the all-skipped live command is recorded here as not run per test, not as behavioral verification or a risk.
**Reconciliation requests**: None. The src-scope guard reported only `src/telescreen/issues/activity/notifier.py` beyond the p12 exceptions; this is the accepted owner amendment under p07, OWNER-E4-01. No p07/p12 or protected paths were edited.
**Hashes**: `impl-plans/active/v13-p17-verification-docs.md` before=1ea6f587d7c4fd43c50bc62da2b42744fc078b48a0edd7b6c75b136d4b3ec659 after=c73f93d7332d3a496ccb079222fd0f02a0b99fbcd621db28122e797a3bb935c5 (main progress-entry edit); retry intent is `tmp/telescreen-v13-20261013-e4/p17/edit-intents-retry.md`. Current hashes for all 18 p17 write-path files are in `tmp/telescreen-v13-20261013-e4/p17/final-hashes.txt`. Four stale inventory entries were refreshed: this plan and owner-edited `tests/e2e/test_flow_v13_detail.py`, `tests/live/v13/test_alert_live.py`, and `tests/live/v13/test_mention_live.py`; the plan change records this retry and the test changes are the accepted session-14 owner fixes.
**Verification**:
- `uv run --frozen ruff format --check .` -> exit 0 (log: `/tmp/telescreen-v13-p17-format-retry.log`).
- `uv run --frozen ruff check .` -> exit 0 (log: `/tmp/telescreen-v13-p17-ruff-retry.log`).
- `uv run --frozen python tools/typelint.py` -> exit 0, 537 files clean (log: `/tmp/telescreen-v13-p17-typelint-retry.log`).
- `uv run --frozen ty check` -> exit 0 (log: `/tmp/telescreen-v13-p17-ty-retry.log`).
- `uv run --frozen pytest -p no:cacheprovider -rs` -> exit 0, 998 passed / 10 skipped / 0 failed (log: `/tmp/telescreen-v13-p17-pytest-retry.log`).
- `uv run --frozen pytest -p no:cacheprovider -q tests/leak/test_leak_matrix_v13.py tests/e2e/test_flow_v13_stream.py tests/e2e/test_flow_v13_archive.py tests/e2e/test_flow_v13_detail.py tests/e2e/test_flow_v13_org.py tests/e2e/test_flow_v13_weekly.py` -> exit 0, 7 passed / 0 failed (log: `/tmp/telescreen-v13-p17-focused-retry.log`).
- `uv build --out-dir /tmp/telescreen-v13-p17-dist-retry` -> exit 0 (log: `/tmp/telescreen-v13-p17-build-retry.log`).
- `cd src/web && bun install --frozen-lockfile` -> exit 0 (log: `/tmp/telescreen-v13-p17-install-retry.log`).
- `cd src/web && bun run typecheck` -> exit 0 (log: `/tmp/telescreen-v13-p17-typecheck-retry.log`).
- `cd src/web && bun run test` -> exit 0, 235 passed / 0 failed (log: `/tmp/telescreen-v13-p17-vitest-retry.log`).
- `cd src/web && bun run build --outDir /tmp/telescreen-v13-p17-web-retry --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-v13-p17-webbuild-retry.log`).
- `cd src/web && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots` -> exit 0; no `state-skipped` or browser-error matches (log: `/tmp/telescreen-v13-p17-shots-retry.log`).
- `! grep -n 'state-skipped' /tmp/telescreen-v13-p17-shots-retry.log` and the browser-error scan -> exit 0 (screenshot log above).
- Src-scope guard with the accepted `notifier.py` exception, protected-path guard, and zero-open-box guard -> exit 0 (command output: foreground retry session; no persistent log was needed).
**Live status (not behavioral verification)**: `uv run --frozen pytest -p no:cacheprovider -rs tests/live/v13` -> exit 0, 2 skipped, 0 passed; both test files are marked `live` and call `require_live_variables` (log: `/tmp/telescreen-v13-p17-live-offline-retry.log`). Per the owner note, 2026-10-13 session 14, the owner-run dev-stack live suite passed 5 tests; no live services were run in this agent environment. The live command is intentionally omitted from the step-result verification list.
**Notes**: Screenshot smoke completed with 89 outputs and no state skips or browser errors. Completion status remains Completed with all five boxes checked. Independent review and serial finalization are not claimed complete here.

### Session: 2026-10-13 P17-LEAK-01 repair
**Tasks Completed**: Expanded `tests/leak/flows_v13.py` to cover valid personal/shared views; comment edits with mentions, subscription and bookmarks; captured fake-SMTP message bodies; DLP-approved and DLP-blocked GitHub create bodies through MockTransport; valid alert creation, fire and history; weekly report delivery; bulk resolve/archive and merge/unmerge; archive expiry and activity; integration status/test; ownership input rejection; member edit and self-token creation. Added five-sink checks after each design flow. Helpers now map to design flows 1-9. The member email uses the reserved `.invalid` domain.
**Tasks In Progress**: Independent test-integrity/adversarial review and serial archive/docs/commit/push are downstream workflow steps.
**Blockers**: None. Live dev-stack tests were not run in this agent environment; `test_alert_live.py` needs `TELESCREEN_TEST_SLACK_BOT_TOKEN`, `TELESCREEN_TEST_SLACK_CHANNEL`, `TELESCREEN_TEST_SMTP_HOST`, `TELESCREEN_TEST_SMTP_PORT`, `TELESCREEN_TEST_MAILPIT_URL`, `TELESCREEN_TEST_EMAIL_TO`; `test_mention_live.py` needs the four SMTP/Mailpit variables. Owner note, session 14, records 5 passed on the dev stack.
**Reconciliation requests**: None. P17 source scope stayed in the two leak-test files; accepted p07 `notifier.py` and p12 wiring files were not edited.
**Hashes**: Initial `flows_v13.py` SHA-256 `fc4deb23e5ddcf2981c66a967b0387b63d1bd34e1c42e05e0b7dee3cb530e4ce`; initial matrix SHA-256 `ff21ca779531b6f0d0b4f41e8b6a164eb71ee404c43b6815fffc067d92ffd0fa`. Each subsequent pre-edit hash and exact intent is recorded in `tmp/telescreen-v13-20261013-e4/p17/edit-intents-leak-retry.md` and its `edit-intents-leak-retry-*.sha256` records. Current hashes for all 18 P17 write-path files are in `tmp/telescreen-v13-20261013-e4/p17/final-hashes.txt`.
**Verification**:
- `uv run --frozen ruff format --check .` -> exit 0, 538 files (log: `/tmp/telescreen-v13-p17-format-leak-retry-final.log`).
- `uv run --frozen ruff check .` -> exit 0 (log: `/tmp/telescreen-v13-p17-ruff-leak-retry-final.log`).
- `uv run --frozen python tools/typelint.py` -> exit 0, 537 files clean (log: `/tmp/telescreen-v13-p17-typelint-leak-retry-final.log`).
- `uv run --frozen ty check` -> exit 0 (log: `/tmp/telescreen-v13-p17-ty-leak-retry-final.log`).
- `uv run --frozen pytest -p no:cacheprovider -rs` -> exit 0, 998 passed / 10 skipped / 0 failed; offline skips are integration/live fixtures (log: `/tmp/telescreen-v13-p17-pytest-leak-retry-final.log`).
- `uv run --frozen pytest -p no:cacheprovider -q tests/leak/test_leak_matrix_v13.py tests/e2e/test_flow_v13_stream.py tests/e2e/test_flow_v13_archive.py tests/e2e/test_flow_v13_detail.py tests/e2e/test_flow_v13_org.py tests/e2e/test_flow_v13_weekly.py` -> exit 0, 7 passed / 0 failed (log: `/tmp/telescreen-v13-p17-focused-leak-retry-final.log`).
- `uv build --out-dir /tmp/telescreen-v13-p17-dist-leak-retry` -> exit 0 (log: `/tmp/telescreen-v13-p17-build-leak-retry.log`).
- `cd src/web && bun install --frozen-lockfile` -> exit 0, no changes (log: `/tmp/telescreen-v13-p17-install-leak-retry.log`).
- `cd src/web && bun run typecheck` -> exit 0 (log: `/tmp/telescreen-v13-p17-typecheck-leak-retry.log`).
- `cd src/web && bun run test` -> exit 0, 235 passed / 0 failed (log: `/tmp/telescreen-v13-p17-vitest-leak-retry.log`).
- `cd src/web && bun run build --outDir /tmp/telescreen-v13-p17-web-leak-retry --emptyOutDir` -> exit 0 (log: `/tmp/telescreen-v13-p17-webbuild-leak-retry.log`).
- `cd src/web && TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots` -> exit 0, 89 outputs (log: `/tmp/telescreen-v13-p17-shots-leak-retry.log`).
- `! grep -n 'state-skipped' /tmp/telescreen-v13-p17-shots-leak-retry.log` -> exit 0; screenshot browser-error scan -> exit 0.
- P17 source-scope guard with accepted notifier exception, protected-path guard, zero-open-box guard, and `wc -l tests/leak/flows_v13.py` -> exit 0; file is 570 lines.
**Notes**: Superseded verification attempts are preserved with their complete logs: focused flow contract failures under `/tmp/telescreen-v13-p17-leak-retry-focused*.log`, initial full suite's TL003 finding under `/tmp/telescreen-v13-p17-pytest-leak-retry.log`, and the first exact-file type check under `/tmp/telescreen-v13-p17-leak-ty-final.log`. All were corrected and the final source-matched gates above pass. SMTP capture is available and exercised; the mention flow asserts a masked issue email, and weekly flow asserts a captured weekly-report email. All five sink classes are checked after each design flow. P17-RETRY-01 remains resolved as previously reviewed; final hash inventory is refreshed after this progress edit. Independent reviews and serial finalization remain downstream.
