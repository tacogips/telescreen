# V11-P21 Leak Matrix Extension and v1.1 End-to-End Flows

**Status**: In Progress
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md section 13 (data handling and leak matrix), sections 4-11 (flows); design-docs/specs/design-masking.md (leak matrix)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p21-leak-e2e`
- planPath: `impl-plans/active/v11-p21-leak-e2e.md`
- wave: 5
- dependsOn: `v11-p19-wiring`, `v11-p20-docs-examples`, `v11-p15-ui-issues`, `v11-p16-ui-ops` (for the final web gates)
- writePaths:
  - `impl-plans/active/v11-p21-leak-e2e.md`
  - `tests/leak/flows_v11.py` (new)
  - `tests/leak/test_leak_matrix_v11.py` (new)
  - `tests/leak/harness.py`
  - `tests/leak/sinks.py`
  - `tests/e2e/test_flow_v11_policy.py` (new)
  - `tests/e2e/test_flow_v11_remediation.py` (new)
  - `tests/e2e/test_flow_v11_owner_health.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

This is the final verification plan of the run. It proves, through the real container
(`tests/leak/harness.py:make_leak_world`, `tests/e2e/harness.py`):

- every v1.1 sink stays free of raw sample values (`tests.support.samples.all_samples`);
- the NEW records and sinks also contain no file names or emails;
- the v1.1 flows work end to end.

The existing v1 matrix (`tests/leak/test_leak_matrix.py`) stays unchanged and must keep
passing. The generic `database_text` sink already scans every ORM table, including the
new ones.

## Non-goals

- No feature code changes. A defect found here -> a reconciliation request naming the
  owning plan's files. Do not patch `src/**`.
- No real network: everything goes through the harness `Recorder` / MockTransport.

## Invariants

- Do not weaken any existing assertion. Do not modify `tests/leak/test_leak_matrix.py`
  or `tests/leak/flows.py`.
- Canary strings are built at runtime from fragments (like `tests/support/samples.py`),
  so gitleaks and the repository fixture scans never see a literal email. The canary
  email domain is `example.com`.

## Deliverables

1. **Canaries**: `tests/leak/harness.py` gains:
   - `CANARY_FILENAME` = a runtime-built name such as `"leakcanary-" + "fname-7f3a" +
     ".txt"`;
   - `CANARY_EMAIL` = a runtime-built `"owner-canary" + "@" + "example.com"`.

   The canned GitHub responses serve a file at path `secrets/<CANARY_FILENAME>` with the
   sample content. CODEOWNERS content maps `secrets/` to the canary email. A push payload
   has `head_commit.author.username` `canary-login` and `timestamp`. The repo JSON has
   `visibility: public`. Keep the existing v1 routes working (add, do not replace).
2. **`sinks.py`**: new `v11_record_text(world) -> tuple[(label, text)]` returns the
   string values of:
   - the `issues` columns `exposure_json`, `exposure_visibility`, `placement_keys_json`,
     `owner_ref`, `policy_name`, `asset_tier`;
   - every column of `placement_policies` except `source_text` (operator configuration,
     scanned separately for raw samples only), `issue_exceptions`, `member_identities`,
     `resource_coverage`, `scan_coverage` and `collector_health`;
   - notification rows whose subject starts with `Telescreen` or that come from the
     digest or exception flows (filter by `actor_ref` `system:digest` /
     `system:issues` / health).
3. **`flows_v11.py`**, flows against the API with admin and member tokens:
   1. `github_public_push`: webhook push -> issue with `critical` severity,
      `exposure_visibility` public, keyed `owner_ref`.
   2. `owner_mapping`: create member `alice` with the canary email as member email (the
      operator-entered contact is allowed in `members`, a v1 table), add manual identity
      `github_login canary-login`, enable owner routing through settings in the world
      builder, then trigger routing (a new push) -> the issue is assigned to alice.
   3. `policy_flow` (design section 4: any matching `violate` policy decides, so the
      selectors must not overlap on the issue):
      - create an `allow` policy whose `github_repos` is `["<owner>/<repo>"]` of the
        issue's repo, and a `violate` policy that does NOT match the issue (a different
        repo, e.g. `["<owner>/other-repo"]`);
      - activate both, `world.drain()` -> the issue is `allowed`, resolution
        `policy_changed`, history has `policy_reevaluated`;
      - disable the allow policy, `world.drain()` -> the issue is `unresolved`, the
        resolution is cleared, history has another `policy_reevaluated`, and an
        `issue_created` event was emitted;
      - re-activate the allow policy and also activate a second `violate` policy that
        matches the same repo, `world.drain()` -> the issue stays `unresolved`
        (violate wins).
   4. `remediation_flow`: `POST /remediation` -> `pending_verification` -> drain (verify
      rescan; the canned repo content now without the secret) -> `verified_removed`.
      Second case on a separate issue: the canned file route now returns 404 at the
      verify rescan -> the issue is `resolved` with resolution `resource_deleted` (design
      section 5 auto-resolution, `removed=true`).
   5. `exception_flow`: request by a member, approve by the admin, then advance
      `FakeClock` past 7 days and tick the scheduler -> `exception_expired` and
      `unresolved`.
   6. `coverage_flow`: a binary blob path -> `resource_coverage` row; `GET` the coverage
      endpoints.
   7. `health_flow`: 3 failing scans (MockTransport 500 for that collector) -> a health
      notification to `team:security` (configure `health_recipients`).
   8. `digest_flow`: set the clock to a digest fire minute and tick -> the digest
      notification for alice.
   9. `police_exposures`: a pull worker submits a 1.1 detect result with
      `resource_exposures`.
   10. `occurrences_flow`: the same token in two files -> `GET /occurrences` lists the
       other issue.
4. **`test_leak_matrix_v11.py`**:
   - Run all flows in one world.
   - Raw samples: assert `find_leaks` is empty on all v1 sinks (`database_text`, `logs`,
     `search_documents`, `outbound_bodies`, `api_responses`) plus `v11_record_text`.
   - Canaries: assert neither canary string appears in `v11_record_text`, in v1.1 API
     responses (responses of the paths `/api/exceptions`, `/api/policies`,
     `/api/collectors/*/coverage*`, `/api/members/*/identities`,
     `/api/issues/*/occurrences`, and the detail fields `exposure` and `owner`), or in
     the outbound bodies of digest, owner-review, exception and health notifications.
     The issue `display_locator` and `display_title` (v1 fields) are excluded from the
     canary check by design. Assert `canary-login` never appears anywhere except in the
     request the test itself sent.
   - Presence checks: each new table has at least one row, and at least one digest and
     one health notification exist.
5. **e2e** (`tests/e2e/test_flow_v11_*.py`), focused functional assertions without the
   leak scans:
   - policy allow and violate with `max_age_days` (FakeClock);
   - remediation including `include_linked` over two linked issues and an inconclusive
     verification (binary content) that keeps `pending_verification` and writes history
     `verification_inconclusive`;
   - owner_review workflow: load `examples/workflows/owner-review.yaml`, run it on a new
     issue, complete the task through `POST /api/human-tasks/{id}/complete` as the
     assignee with outcome `remove` -> issue `pending_verification`; `not_mine` -> team
     reassignment;
   - escalation `min_risk` stops below the threshold;
   - key rotation: version 2 active -> new findings carry version 2, the issue count is
     unchanged (no duplicate issues) and occurrences across versions are not linked.

## Pitfalls

- Use `world.drain()` (or the e2e harness equivalent) to run engine tasks and scheduler
  ticks; never sleep.
- Notification delivery is a separate task. Assert on the stored notification rows and
  the recorded outbound bodies after draining.
- If a flow cannot run because a feature is missing or broken, keep the failing test
  unchanged (no `xfail`, `skip` or weakened assertion), write a reconciliation request
  naming the owning plan and file, and report the plan as blocked. Every flow is an
  acceptance criterion of the run, so it must pass before this plan completes. Do not
  silently drop coverage.

## Verification

Conventions section 7 with `<tests>` = `tests/leak tests/e2e`. Then the final
whole-repository gates, which must all exit 0:

- `uv run --frozen ruff format --check --no-cache . > /tmp/telescreen-v11-p21-leak-e2e-format.log 2>&1; echo "exit=$?"`
- `uv run --frozen ruff check --no-cache . > /tmp/telescreen-v11-p21-leak-e2e-ruff.log 2>&1; echo "exit=$?"`
- `uv run --frozen python tools/typelint.py > /tmp/telescreen-v11-p21-leak-e2e-typelint.log 2>&1; echo "exit=$?"`
- `uv run --frozen ty check > /tmp/telescreen-v11-p21-leak-e2e-ty.log 2>&1; echo "exit=$?"`
- `uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v11-p21-leak-e2e-pytest.log 2>&1; echo "exit=$?"`
- `uv build --out-dir /tmp/telescreen-v11-p21-leak-e2e-dist > /tmp/telescreen-v11-p21-leak-e2e-build.log 2>&1; echo "exit=$?"`
- in `src/web`:
  - `bun install --frozen-lockfile`;
  - `bun run typecheck`;
  - `bun run test`;
  - `bun run build --outDir /tmp/telescreen-v11-p21-leak-e2e-web --emptyOutDir`;
  - `bun run screenshots` (35 PNGs in `tmp/ui-screenshots/`, zero browser errors). This
    is an acceptance criterion of the run: a missing browser or any browser error is a
    blocker to report, not a gap.

  Each runs with a log under `/tmp/telescreen-v11-p21-leak-e2e-web-*.log` and must show
  `exit=0`.

## Completion Criteria

- [ ] Canaries, the sink, 10 flows, the v1.1 matrix test and 3 e2e files are added; the
      v1 matrix is unchanged and passing.
- [ ] All whole-repository Python and web gates exit 0.
- [ ] Progress Log complete.

## Progress Log

### Session: 2026-10-10 22:22
**Tasks Completed**: Added runtime filename/email canaries and public/binary GitHub fixtures in `tests/leak/harness.py`; added `v11_record_text` in `tests/leak/sinks.py`; added ten named v1.1 flow functions and `tests/leak/test_leak_matrix_v11.py`; added the three assigned v1.1 E2E modules. The unchanged v1 matrix passed.
**Tasks In Progress**: The v1.1 leak matrix still fails its required owner-digest notification assertion. The policy, remediation, and owner/health E2E files do not yet cover every scenario listed in the plan (max-age decision, inconclusive verification, owner-review task outcomes, min-risk escalation, and key rotation). Frozen web install and screenshot smoke were not run because the assigned `artifactRoots` is empty: install writes `src/web/node_modules`, and the screenshot script writes `tmp/ui-screenshots/`, neither of which is an authorized artifact root.
**Blockers**: Required final verification is incomplete: current full pytest has one failure in `tests/leak/test_leak_matrix_v11.py` (missing `Telescreen owner digest`; 560 passed, 1 failed, 4 skipped). The install and screenshot gates need an amended checkpoint declaring their generated output roots. Resume after the digest/scheduler reconciliation is resolved, the remaining E2E cases are implemented, and artifact roots are declared for web dependencies and screenshots.
**Reconciliation requests**: `v11-p06-issues-remediation` / `src/telescreen/issues/digest.py` and `src/telescreen/issues/maintenance.py`, with `v11-p19-wiring` / `src/telescreen/wiring.py`: investigate why scheduler ticks log `TelescreenError` and no owner-digest notification is persisted for an open issue assigned to `member:alice`; expected evidence is a passing p21 leak matrix with a `Telescreen owner digest` notification after scheduler tick and worker drain.
**Hashes**: `impl-plans/active/v11-p21-leak-e2e.md` before=dc46d601fec7b07fe934da07eff1d11b6844195fef883a0900f5d8c5677c21df after=7eaffbcbab7c845bcccca8c414c4c2af1bd2d1c1ba4259bde48099675a210682; `tests/leak/harness.py` before=cb45ea79243d45857d7151c489a4183d98e10065f9d90ac943ba8011396c3544 after=3bbd1cfca8b90713205c488b4261befcadc35bbb0828266cffc032c7f11662d5; `tests/leak/sinks.py` before=c4551b9cc90f269c1c663c95050b2311d04aba2b48f794f0718f908ac41da0e5 after=cbb3fcb4ce38f1e537258097547c5af7ed7d5c1074756fc83fe5287aed308625; `tests/leak/flows_v11.py` before=absent after=d34b8f2e8be3e723746c526b27187352239f735df2734276944f5e04958c9837; `tests/leak/test_leak_matrix_v11.py` before=absent after=297dac38e96d56ac52db26ba6a1b89967c7f32c8bbaaea6dfb3bd9c19d080ed0; `tests/e2e/test_flow_v11_policy.py` before=absent after=8663790961440ca6c6ae96712dc39efb8beed1cb17f9da08fb39311f0f5fe902; `tests/e2e/test_flow_v11_remediation.py` before=absent after=48bf89365c3db608ec38c7900793835637923234d3b762fa91f5b21701b6796f; `tests/e2e/test_flow_v11_owner_health.py` before=absent after=5d983b7638d77a1b2f3d30779211c1c81ff193e493008125280d3f70d511316c.
**Verification**: `uv run --frozen ruff format --check --no-cache .` -> exit 0 (log: `/tmp/telescreen-v11-p21-leak-e2e-format-final3.log`); `uv run --frozen ruff check --no-cache .` -> exit 0 (log: `/tmp/telescreen-v11-p21-leak-e2e-ruff-final3.log`); `uv run --frozen python tools/typelint.py` -> exit 0 (log: `/tmp/telescreen-v11-p21-leak-e2e-typelint-final3.log`); `uv run --frozen ty check` -> exit 0 (log: `/tmp/telescreen-v11-p21-leak-e2e-ty-final3.log`); `uv run --frozen pytest -p no:cacheprovider` -> exit 1, 560 passed / 1 failed / 4 skipped (log: `/tmp/telescreen-v11-p21-leak-e2e-pytest-final3.log`); `uv build --out-dir /tmp/telescreen-v11-p21-leak-e2e-dist-final3` -> exit 0 (log: `/tmp/telescreen-v11-p21-leak-e2e-build-final3.log`); web typecheck -> exit 0 (log: `/tmp/telescreen-v11-p21-leak-e2e-web-typecheck.log`); web test -> exit 0, 146 passed (log: `/tmp/telescreen-v11-p21-leak-e2e-web-test.log`); web build to `/tmp` -> exit 0 (log: `/tmp/telescreen-v11-p21-leak-e2e-web-build.log`); unchanged v1 leak matrix -> exit 0, 4 passed (log: `/tmp/telescreen-v11-p21-v1-matrix-final.log`). `bun install --frozen-lockfile` and `bun run screenshots` were not run due the missing artifact-root authorization described above.

**Owner resolution (2026-10-10)**: P21-DIGEST-MISSING was a real integration bug, fixed by the owner: `issues/exceptions.py`, `issues/verification.py` and `issues/digest.py` resolved the recipient and then passed resolved addresses to the notification sender, which expects recipient references; the sender raised recipient_unknown and the exception-maintenance tick rolled back before the digest. New `telescreen/issues/recipient_channel.py` passes the reference and skips unknown recipients (tests in `tests/issues/test_recipient_channel.py`). `pytest tests/leak tests/issues tests/api` passes (89 passed) including `tests/leak/test_leak_matrix_v11.py`. Re-run the full gate; not a blocker. Report per conventions 7a.
