# V12-P12 Verification and Docs: Leak Matrix, E2E Flows, Live Tests, Examples, README

**Status**: Not Started
**Design Reference**: design-docs/specs/design-v1-2-notifications-and-identity.md sections 6.5, 7, 8, 10 (E2E flows T1, T2, A1, A2); design-docs/specs/design-masking.md "v1.2 additions"; design-docs/specs/design-ui-sentry.md 10.4
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p12-verification-docs`
- planPath: `impl-plans/active/v12-p12-verification-docs.md`
- wave: 5
- dependsOn: `v12-p11-wiring-cli`, `v12-p09-ui-pages`, `v12-p03-ui-foundation`
- writePaths:
  - `impl-plans/active/v12-p12-verification-docs.md`
  - `tests/leak/flows_v12.py` (new)
  - `tests/leak/test_leak_matrix_v12.py` (new)
  - `tests/leak/harness.py` (only if an OIDC or SMTP seam is needed; keep v1/v1.1 behavior)
  - `tests/e2e/test_flow_v12_notifications.py` (new)
  - `tests/e2e/test_flow_v12_identity.py` (new)
  - `tests/live` (new directory)
  - `examples/workflows`
  - `tests/examples`
  - `examples/compose/README.md`
  - `README.md`
  - `src/web/node_modules` (artifact)
  - `tmp/ui-screenshots` (artifact)
- sharedPaths: none
- artifactRoots: `src/web/node_modules`, `tmp/ui-screenshots`

Read `impl-plans/conventions.md` first. This plan runs alone in the last wave. It is the
serial owner of `README.md`.

## Intent and Context

Prove v1.2 end to end and document it for the owner's live run:

- the leak matrix extension (design section 8, flows 1-5);
- the E2E flows T1, T2, A1 and A2;
- `tests/live` for Mailpit, Slack and Keycloak, skipped offline;
- a v2 example workflow;
- the dev-services README;
- the README features;
- the final full CI, web gates and screenshot smoke.

Imitate:

- `tests/leak/flows_v11.py` and `tests/leak/test_leak_matrix_v11.py` (flow functions
  over `LeakWorld`; sink assertions via `tests/leak/sinks.py` and
  `tests.support.samples.assert_no_leaks`);
- `tests/e2e/test_flow_v11_*.py` and `tests/e2e/harness.py`;
- `tests/examples/test_examples.py`.

## Non-goals

- No product code changes. A defect -> a Reconciliation request naming the owning plan
  and file; do not patch `src/`.
- Do not edit `examples/compose/dev-services.yml` or `keycloak/telescreen-realm.json`
  unless a live test cannot work otherwise. The realm already allows direct grants and
  ports 5174/8000. Any edit must be justified in the Progress Log.
- Never read or print `~/.config/telescreen/slack-test.env`. Live tests read only the
  environment variables.

## Invariants

- The default `uv run pytest` passes offline; every live test is skipped when its
  variables are absent.
- Sample secrets come from `tests.support.samples` (assembled at runtime); no literal
  secrets anywhere (gitleaks).
- The OIDC tokens used in the leak flows are minted at runtime by `tests/support/oidc.py`.

## Deliverables

1. **Leak matrix v1.2** (`flows_v12.py`, `test_leak_matrix_v12.py`), using the real
   `build_container` plus the v1 seams, a MockTransport IdP, a Slack MockTransport and a
   fake SMTP transport if the harness supports it (else Slack only, with a recorded
   note). Flows:
   1. template create, validate and preview with each sample in each text field -> 422,
      no `notification_templates` rows containing samples, `template.rejected` audit
      field names only;
   2. a v2 workflow with a set (Slack target, email target, an unreachable target) and
      an inline body containing samples; run it and deliver -> masked rows, outbound
      payloads, step outputs (counts), and a `skipped` row present;
   3. role change via the API, a `last_admin` 409, token create and revoke;
   4. OIDC sign-in for admin, viewer, a no-role user (403) and an invalid token (401):
      the minted access tokens, ID token and `sub` appear in no table, log, search
      document, outbound body or API response;
   5. the notify-test service path with inline samples.

   Assert with every existing sink function. Add the token and sub strings to the
   searched values for flow 4.
2. **E2E** (`tests/e2e/test_flow_v12_notifications.py`: T1 and T2;
   `tests/e2e/test_flow_v12_identity.py`: A1 and A2):
   - **T1**: an admin creates and activates a custom `workflow_notify` template; a v2
     workflow with a two-target set; per-target rows and output counts.
   - **T2**: human task `notify_set` notifications plus an escalation tier with
     `notification_set` after an SLA breach (`FakeClock`).
   - **A1**: alice (admin), bob (responder), carol (viewer), dave (no role) as
     locally minted tokens with `realm_access.roles` and `groups`, `create`
     provisioning, and team map `security=secops`:
     - session results;
     - bob can PATCH an issue, carol cannot;
     - alice can create a template;
     - alice gets an idp membership of `secops`;
     - a member-bound write token of carol gets 403 on PATCH (intersection).
   - **A2**: demoting the last admin via the API -> 409; IdP demotion audited with
     `source: idp`; `telescreen member set-role` recovery via the CLI entry function.
3. **`tests/live/`**:
   - `conftest.py`: skip helpers per variable group;
   - `test_mailpit_email.py`, `test_slack_delivery.py`, `test_keycloak_oidc.py` exactly
     as in design 7.2 (marker `live`);
   - Keycloak: the password grant on `telescreen-ui` for alice/bob/carol/dave (dev
     passwords from the realm file, not hard-coded secrets; build them as `f"{user}-dev-pass"`);
   - plus the scripted code+PKCE form login for alice;
   - Slack: `auth.test` scope header check before `conversations.history`.
4. **Examples**:
   - `examples/workflows/notification-sets.yaml` (v2: one set with
     `slack_channel:C0123ABCDE` and `email:secops-leads@example.invalid` targets, a
     notify step referencing the set and `system.workflow_notify`, and a human task with
     `notify_set`);
   - `examples/workflows/escalation-policies.json` gains a tier with
     `notification_set`;
   - `tests/examples/test_examples.py` loads them.
5. **`examples/compose/README.md`**: a "Local test services (dev-services.yml)" section:
   - start and stop;
   - Mailpit SMTP and UI ports;
   - Keycloak realm users and roles (alice, bob, carol, dave), the issuer
     host-consistency note (127.0.0.1 everywhere) and the OIDC env block of design 6.5;
   - the test PostgreSQL URL, `telescreen migrate` and team seeding with an admin
     token;
   - the Slack test env as `env:` refs sourced from `~/.config/telescreen/slack-test.env`
     (the file is not read by Telescreen);
   - `telescreen notify test` examples;
   - the `TELESCREEN_TEST_*` live-test variables and `uv run pytest -m live tests/live`.
6. **`README.md`**: a v1.2 features section (templates, notification sets, roles and
   permission matrix link, OIDC SSO, live verification, the new CLI commands), linking
   to the design doc.

## Pitfalls

- Live tests must use unique subject tokens per run (`new_id()`) and must not depend on
  the Mailpit inbox being empty.
- The Keycloak access token `iss` uses the host in `TELESCREEN_TEST_KEYCLOAK_URL`.
  Configure `TELESCREEN_OIDC_ISSUER` from that same URL plus `/realms/telescreen`.
- The leak matrix must not stub services in a way that bypasses the production
  rendering, DLP or auth paths (design-masking: only the listed seams).
- E2E OIDC needs no harness edit: `tests/e2e/harness.py:make_world` already takes
  `extra_env` (set the `TELESCREEN_OIDC_*` variables with an `https://idp.example.invalid`
  issuer) and `OutboundRecorder.handlers` (register that host to serve discovery and JWKS
  from `tests/support/oidc.py`). Do not edit `tests/e2e/harness.py` (not in writePaths).
- The new v2 example `examples/workflows/notification-sets.yaml` is also picked up by the
  `*.yaml` globs in `tests/workflow/test_v11_workflow.py` (compiled JSON round-trip;
  p05 makes it hold for v2); it must load and round-trip without editing that test.

## Verification

Run with `set -o pipefail` and record each exit status and log path:

```bash
uv run --frozen ruff format --check . > /tmp/telescreen-v12-p12-verification-docs-format.log 2>&1; echo "exit=$?"
uv run --frozen ruff check --no-cache . > /tmp/telescreen-v12-p12-verification-docs-ruff.log 2>&1; echo "exit=$?"
uv run --frozen python tools/typelint.py > /tmp/telescreen-v12-p12-verification-docs-typelint.log 2>&1; echo "exit=$?"
uv run --frozen ty check > /tmp/telescreen-v12-p12-verification-docs-ty.log 2>&1; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v12-p12-verification-docs-pytest.log 2>&1; echo "exit=$?"
uv build --out-dir /tmp/telescreen-v12-p12-verification-docs-dist > /tmp/telescreen-v12-p12-verification-docs-build.log 2>&1; echo "exit=$?"
cd src/web && bun install --frozen-lockfile && bun run typecheck && bun run test && bun run build --outDir /tmp/telescreen-v12-p12-verification-docs-web --emptyOutDir > /tmp/telescreen-v12-p12-verification-docs-web.log 2>&1; echo "exit=$?"
cd src/web && bun run screenshots > /tmp/telescreen-v12-p12-verification-docs-shots.log 2>&1; echo "exit=$?"
```

Every command must exit 0. pytest reports the live tests as skipped (not failed) and the
5 offline `integration` skips of the fcfb198 baseline (tests/search/test_search.py,
tests/audit/test_audit.py, tests/workflow/mapreduce/test_postgres.py,
tests/db/test_migration.py, tests/db/test_v12_migration.py); passed count at or above 784. Screenshots: 43 images, zero page or console
errors. A missing browser is a recorded verification gap, not a pass.

## Completion Criteria

- [ ] Deliverables 1-6 complete.
- [ ] All verification commands exit 0 (logs recorded); 43 screenshots.
- [ ] No `src/` file changed by this plan (`git status --porcelain` check).
- [ ] Progress Log complete.

## Progress Log
