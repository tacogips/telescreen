# V13-P17 Verification and Docs: v1.3 Leak Matrix, E2E Flows, Live Checks, Strict Screenshot Smoke, README and Status

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 7 (safety rules), 14, 16, 17; design-docs/specs/design-ui-sentry.md 11.3
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

## Plan Metadata

- planId: `v13-p17-verification-docs`
- planPath: `impl-plans/active/v13-p17-verification-docs.md`
- wave: 5
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
  - `README.md`
  - `impl-plans/README.md`
  - `design-docs/specs/design-v1-3-sentry-parity.md`
  - `design-docs/specs/design-v1-3-sentry-parity-review.md`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Prove v1.3 end to end, and document it:

- the leak matrix extension (design 14, flows 1-9);
- E2E flows E1-E5 (design 16), including the merge-into-archived assertions;
- env-gated live checks;
- the strict screenshot smoke over the realistic seed;
- the README v1.3 features, the plan index and the design status lines.

Archiving the v13 plans into `impl-plans/completed/` is the serial finalization step
after the fanout. This plan does not move plan files.

Imitate:

- `tests/leak/flows_v12.py` and `test_leak_matrix_v12.py` (a `make_leak_world` per flow;
  assert all sinks from `tests/leak/sinks.py`: `database_text`, `logs`,
  `search_documents`, `outbound_bodies`, `api_responses`);
- `tests/e2e/test_flow_v12_notifications.py` and `harness.py`;
- `tests/live/test_slack_delivery.py` and `test_mailpit_email.py`
  (`require_live_variables`, marker `live`).

## Non-goals

- No feature code changes. A defect becomes a Reconciliation request naming the owning
  plan and file. If the request blocks acceptance, report it as a blocker.
- No edits to `tests/leak/harness.py` or `sinks.py`, or to v1/v11/v12 flows.
- No fixture edits in `src/web` (p03 owns them).

## Invariants

- Sample secrets come only from `tests.support.samples` (assembled at runtime; gitleaks).
- Live tests are under `tests/live/v13/`, marked `live`, and skipped unless their
  variables are set. The offline `uv run pytest` must pass with them skipped.
- Live variables: Slack `TELESCREEN_TEST_SLACK_BOT_TOKEN` and
  `TELESCREEN_TEST_SLACK_CHANNEL`; Mailpit `TELESCREEN_TEST_SMTP_HOST`,
  `TELESCREEN_TEST_SMTP_PORT`, `TELESCREEN_TEST_MAILPIT_URL` and
  `TELESCREEN_TEST_EMAIL_TO`. These are the same names as the v1.2 live tests.

## Deliverables

1. **`tests/leak/flows_v13.py`** and **`test_leak_matrix_v13.py`**: flows 1-9 of design
   14, exactly as listed there. Flow 2 names the activity response and the comment,
   mention, subscription and bookmark rows. Each flow feeds the sample secrets into every
   user-controlled field it touches and asserts that no sample appears in any sink.
2. **E2E flows**:
   - `test_flow_v13_stream.py` (E1): seeded issues; counts; queries with negation,
     `has:`, dates and comparisons; a shared view and a default view; bulk resolve by
     query; merge and unmerge.
   - `test_flow_v13_archive.py` (E2):
     - archive until 5 findings, then ingest, `escalating`, and an `issue_escalating`
       alert rule that fires once per interval (a second escalation inside the interval
       -> `suppressed_interval`);
     - duration expiry through the scheduler tick with `FakeClock`;
     - merge into an archived primary (P `until_findings` 5, child with 40 historical
       findings): P stays archived and `archive_until` is unchanged; 4 new findings leave
       P archived and the 5th escalates it;
     - unmerge: the archived child escalates only after 5 findings of its own.
   - `test_flow_v13_detail.py` (E3):
     - navigation over 120 findings (oldest, next, latest; `total` 120);
     - tags;
     - a mention notifies the mentioned member by email (fake SMTP) according to their
       settings;
     - subscriber activity notifications exclude the actor.
   - `test_flow_v13_org.py` (E4): an ownership rule routes a new issue to its team;
     deactivation releases the member's open issues; self token create and use.
   - `test_flow_v13_weekly.py` (E5): a weekly scheduler tick enqueues once; the handler
     sends one report per opted-in member.
3. **`tests/live/v13/`**:
   - `test_alert_live.py`: an alert rule (`issue_created`, targets `slack_channel:<env
     channel>` on slack and `email:<env to>` on email, interval 60) fires on a synthetic
     leak ingested through the push path. The Slack API shows one message, and Mailpit
     shows one mail. A second synthetic leak within the interval delivers nothing new.
   - `test_mention_live.py`: a member with mentions=email and the Mailpit address is
     mentioned. Mailpit receives exactly one mail whose body contains the short id and
     none of the sample values.
4. **`README.md`**: a "Features (v1.3)" section (stream tabs and query language, saved
   views, bulk and merge, archive and escalating, issue detail, alert rules, notification
   settings and weekly report, dashboard, organization pages, palette and shortcuts). Add
   the v1.3 API endpoints to the API section, the new UI pages, the new environment
   variables (`TELESCREEN_ESCALATING_*`, `TELESCREEN_WEEKLY_REPORT_CRON`,
   `TELESCREEN_GITHUB_ISSUES_*`), and the onboarding docs anchors the UI links to.
5. **Docs status**:
   - `impl-plans/README.md`: list the v1.3 plans with wave, dependencies and design
     reference, in the v1.2 table style;
   - the status lines of `design-v1-3-sentry-parity.md` and
     `design-v1-3-sentry-parity-review.md`: set to "implemented" with the date.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check . 2>&1 | tee /tmp/telescreen-v13-p17-format.log; echo "exit=$?"
uv run --frozen ruff check . 2>&1 | tee /tmp/telescreen-v13-p17-ruff.log; echo "exit=$?"
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p17-typelint.log 2>&1; echo "exit=$?"
uv run --frozen ty check 2>&1 | tee /tmp/telescreen-v13-p17-ty.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p17-pytest.log 2>&1; echo "exit=$?"
uv build --out-dir /tmp/telescreen-v13-p17-dist > /tmp/telescreen-v13-p17-build.log 2>&1; echo "exit=$?"
cd src/web && bun install --frozen-lockfile > /tmp/telescreen-v13-p17-install.log 2>&1; echo "exit=$?"
bun run typecheck > /tmp/telescreen-v13-p17-typecheck.log 2>&1; echo "exit=$?"
bun run test > /tmp/telescreen-v13-p17-vitest.log 2>&1; echo "exit=$?"
bun run build --outDir /tmp/telescreen-v13-p17-web --emptyOutDir > /tmp/telescreen-v13-p17-webbuild.log 2>&1; echo "exit=$?"
TELESCREEN_SCREENSHOTS_STRICT=1 bun run screenshots > /tmp/telescreen-v13-p17-shots.log 2>&1; echo "exit=$?"
```

Expected:

- every command exits 0;
- the pytest log shows the v13 leak and flow tests passed, and `tests/live/v13` skipped
  offline;
- the screenshot log has no `state-skipped` line and no page or console error, and covers
  every state of design 16 in light and dark.

Owner-run (optional evidence; not a pass gate when the variables are unset):

```bash
uv run --frozen pytest -m live tests/live/v13 -p no:cacheprovider
```

## Completion Criteria

- [ ] Leak flows 1-9 and E1-E5 pass offline, including the safety rule 5 assertions.
- [ ] Live tests exist, are marked and skipped offline.
- [ ] The strict screenshot smoke passes.
- [ ] The README, plan index and design status lines are updated.
- [ ] The Progress Log has every command with its exit status and log path.

## Progress Log

(append sessions per conventions section 8)
