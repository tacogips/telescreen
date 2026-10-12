# V13-P17 Verification and Docs: v1.3 Leak Matrix, E2E Flows, Live Checks, Strict Screenshot Smoke, README and Status

**Status**: Ready
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

- [ ] Leak flows 1-9 and E1-E5 pass offline, including the safety rule 5 assertions.
- [ ] Live tests exist under `tests/live/v13`, are marked `live` and skipped offline; the
  live dev-stack command result is recorded (passed or "not run" per test).
- [ ] The strict screenshot smoke passes with no `state-skipped` line.
- [ ] The README, plan index and design status lines are updated.
- [ ] The Progress Log has per-edit pre/post SHA-256 and every command with its exit
  status and log path.

## Progress Log

(append sessions per conventions section 8)
