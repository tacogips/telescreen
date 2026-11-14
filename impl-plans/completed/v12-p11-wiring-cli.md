# V12-P11 Wiring, CLI (notify test, member set-role, token --member), Route-Walk Test

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-2-notifications-and-identity.md sections 5.2, 7.1, 7.4, 10; design-docs/specs/command.md (v1.2 rows)
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p11-wiring-cli`
- planPath: `impl-plans/active/v12-p11-wiring-cli.md`
- wave: 4
- dependsOn: `v12-p04-notification-templates`, `v12-p05-workflow-notification-sets`, `v12-p06-rbac-enforcement`, `v12-p07-directory-roles`, `v12-p08-oidc-identity`, `v12-p10-api-routers`
- writePaths:
  - `impl-plans/active/v12-p11-wiring-cli.md`
  - `src/telescreen/wiring.py`
  - `src/telescreen/wiring_v12.py` (new)
  - `src/telescreen/cli.py`
  - `src/telescreen/cli_v12.py` (new)
  - `tests/e2e/test_api_routes.py`
  - `tests/e2e/test_wiring_v12.py` (new)
  - `tests/e2e/test_route_permissions.py` (new)
  - `tests/e2e/test_cli_v12.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Bind every v1.2 implementation into the container:

- the DB template resolver and catalog;
- the OIDC verifier, sign-in service and provisioner;
- the role service;
- the three new routers;
- `ApiContext.verifier` / `identities`.

Add the CLI commands, and add the full-app route-walk and scope-compatibility tests
that guard R1.

Imitate:

- `src/telescreen/wiring_v11.py` (builder functions returning small frozen dataclasses);
- `src/telescreen/wiring.py:build_container` (router tuple, `Container` fields);
- `src/telescreen/cli.py:_run_async_command` (the `token` branch);
- `tests/e2e/test_wiring_v11.py`, `tests/e2e/test_api_routes.py`.

## Non-goals

- `api/app.py` needs no change (it includes `container.routers`).
- No service changes. A service bug becomes a Reconciliation request to its owning plan.
- No live-service code (p12).

## Invariants

- `wiring.py` stays under 1000 lines (733 now); new construction goes into
  `wiring_v12.py`.
- `Container` gains one field at the end: `v12: WiringV12`, holding `templates:
  TemplateService`, `notifications: NotificationService`, `delivery:
  NotificationDeliveryHandler`, `roles: RoleService` and `sign_in: SignInService |
  None`. Existing positional construction is updated in one place only.
- With `settings.oidc is None`, there is no verifier, `/api/auth/session` returns 404,
  and behavior is otherwise v1.1-identical for API tokens.

## Deliverables

1. **`wiring_v12.py`**: `build_v12(settings, primitives, directory pieces, gate, queue,
   resolver, http) -> WiringV12Parts`:
   - `DbTemplateResolver` and `TemplateService`;
   - `RoleService`, `DirectoryProvisioner(fingerprinter, ...)`;
   - when `settings.oidc`: `OidcProviderClient(http, settings.oidc, clock)`,
     `OidcBearerVerifier` and `SignInService(sessions, ...)`;
   - the routers `build_notification_templates_router`, `build_users_router`
     (`idp_role_mapping = settings.oidc is not None`) and `build_auth_router`.
2. **`wiring.py`**:
   - `ApiContext(sessions, clock, audit, verifier, identities)`;
   - `NotificationService(..., templates=DbTemplateResolver(...))`;
   - `WorkflowService(..., templates=template_service)`;
   - append the three v12 routers to `routers`;
   - fill the `Container.v12` field.
   - Keep the import order and the existing names.
3. **`cli_v12.py`** + **`cli.py`** parser additions (exit codes per command.md):
   - `notify test --channel email|slack --to REF [--template NAME]`:
     - an unknown template -> stderr "unknown template", exit 2;
     - in one transaction: `container.v12.notifications.send(NotificationRequest(channel,
       (ref,), "", "", "system:cli", event=<template event or workflow_notify>,
       template_name=name or "system.workflow_notify",
       context=synthetic_context(event), record_unreachable=True))`;
     - then, for each queued id, `await container.v12.delivery.handle(ClaimedTask-like
       value for that id)`. Build it with the real `ClaimedTask` constructor and
       attempt 1, and use max_attempts 1 so failures end as `failed`;
     - print one JSON line per row (`notification_id`, `channel`, `status`,
       `dlp_verdict`, `error_code`) using `dump_json`;
     - exit 0 when all rows are `sent`, else 1.
   - `member set-role HANDLE ROLE` (`none` allowed): `RoleService.set_role(...,
     actor_ref="system:cli", source=cli)`; `LAST_ADMIN` -> stderr "last admin cannot be
     removed", exit 1; unknown handle -> exit 1.
   - `token create --member HANDLE`: resolve via `TokenService.member_id_for_handle`;
     unknown -> exit 2.
4. **`tests/e2e/test_route_permissions.py`**:
   - build the full app with `build_container` (SQLite, in-memory search, OIDC
     configured through `tests/support/oidc.py` and a MockTransport);
   - walk `app.routes`: for every `fastapi.routing.APIRoute`, collect dependency callables
     recursively from `route.dependant` (including `route.dependencies`). Exactly one
     must be a `PermissionDependency` or an `ExternalAuthDeclaration`;
   - the set of external `(method, path, kind)` must equal the allow-list of design 5.1
     (health, auth config, auth session, the collectors webhook, the 6 police v1 routes);
   - scope compatibility: a hard-coded table `(method, path) -> frozenset of v1 scopes
     accepted` for every pre-v1.2 route (derived from the p06 table and v1
     `require_scope`). For each route, compute `{s for s in ApiScope if
     route_permission in scope_permissions({s})}` and assert equality;
   - the test fails with the offending method and path listed.
5. **`tests/e2e/test_wiring_v12.py`**:
   - the container builds with and without OIDC;
   - `/api/auth/config` reflects the settings;
   - a JWT minted by the test IdP for a provisioned admin reaches
     `/api/notification-templates` (200);
   - a viewer JWT gets 403 on template create;
   - `NotificationService` resolves a DB template activated through the API.
6. **`tests/e2e/test_cli_v12.py`**:
   - `notify test --channel slack --to slack_channel:C0123ABCDE`, with a container
     built by `build_container(..., http=httpx.AsyncClient(transport=MockTransport))`
     returning `{"ok":true,"channel":"C0123ABCDE","ts":"1.1"}` and a Slack token env
     ref, run through `_run_async_command(container, args)` -> exit 0 and a JSON line
     with status `sent`. No SMTP injection is needed;
   - an unknown template -> 2;
   - an unreachable `email:` ref on Slack -> 1 and a `skipped` line;
   - `member set-role` last admin -> 1;
   - `token create --member` binds the member.
7. **`tests/e2e/test_api_routes.py`**: add the new list routes to the assembled-route
   200 checks.

## Pitfalls

- The route walk must include routes added by every router, including the police
  contract routes, and must exclude the static SPA `Route` (not an `APIRoute`).
- `ClaimedTask` fields: read `ports/workflow.py` for the exact constructor. Do not invent
  a fake type.
- The CLI must not print the template text or the rendered body, only the row metadata
  listed.
- When OIDC is configured but discovery is unreachable at startup, building the container
  must not fail (the verifier is lazy).

## Verification

Conventions section 7 with `<src>` = `src/telescreen/wiring.py src/telescreen/wiring_v12.py
src/telescreen/cli.py src/telescreen/cli_v12.py` and `<tests>` = `tests/e2e`. Also run the
full offline suite, which must exit 0 at this wave:

```bash
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v12-p11-wiring-cli-pytest-all.log 2>&1; echo "exit=$?"
uv run --frozen python tools/typelint.py > /tmp/telescreen-v12-p11-wiring-cli-typelint-all.log 2>&1; echo "exit=$?"
uv run --frozen ty check > /tmp/telescreen-v12-p11-wiring-cli-ty-all.log 2>&1; echo "exit=$?"
```

## Completion Criteria

- [x] Deliverables 1-7 implemented; `wiring.py` is 756 lines.
- [x] The full-app route-walk and scope-compatibility tests pass.
- [x] The full pytest, typelint and ty runs exit 0 (log paths recorded).
- [x] INT-D4-M01B: CLI workflow validation uses `source_has_raw_values`; explicit email targets validate while free-text emails and sample secrets are rejected without echoing values.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-11 15:00
**Tasks Completed**: Implemented the v1.2 composition seam and CLI. `wiring_v12.py`
constructs the shared DB template resolver/catalog, role service and provisioner, optional
lazy OIDC verifier/sign-in service, and the templates/users/auth routers. `wiring.py` now
injects the resolver/catalog and optional verifier/identities, registers the new routers,
and exposes real notification, delivery, role, template and sign-in services under
`Container.v12`. `cli_v12.py` plus parser dispatch support `notify test`, `member set-role`,
and `token create --member`. Added app route/scope coverage, wiring and OIDC E2E checks, CLI
delivery and role/token checks, and the two list-route smoke paths.
Added explicit no-OIDC session 404, unknown member role failure and unknown token member
exit-code assertions in the final test pass.
**Tasks In Progress**: None in this plan. Formal integrity/adversarial and serial integration
reviews, downstream p12 documentation, staging and commit remain later workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**:
- `impl-plans/active/v12-p11-wiring-cli.md` before=98194b45289b70965c938f16d64c0a70169ca3663d0c4616c7b6016e4b567ad3; final after hash is recorded in `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/final-hashes-completion.txt`.
- `src/telescreen/wiring.py` before=58c4ecedc4f1bb5095b2bdb375c841e160d4098a06e24173a579914efafc3d6d after=1aa86c2e29ee9c8d9daea31f637ff9f1d44c0cf2a4339a6ab3fc1979bdd2bc83.
- `src/telescreen/cli.py` before=852a9e5bac0f5cf280ef87454710a35dd484ef46e1424a739e8ce685528bfc02 after=ef7f1358077313ec637a8b88ed1960fb137efc8eb59fc778b1f372d3a6c2b759.
- `tests/e2e/test_api_routes.py` before=7d6465ba286fc6797badec2ab4d74da8fb6054c7be1f0e2b5640a18b7db157f6 after=dbba51619427bd582eac78599b92dda932f8b99acef6e146465543fa9d64ada2.
- New files: `src/telescreen/wiring_v12.py` a817c45e7f5c94a1d3786e1f9879a46bbc0b03fb71dc159161de30c4425499bf; `src/telescreen/cli_v12.py` d143ea0cf2064303a364bfb1ced14a775b6736e7af9c3c635650e5a13314145d; `tests/e2e/test_wiring_v12.py` 890a37d05da207adc5bb3df151e35131e559346cf1b5f708f5e5ec04a451336f; `tests/e2e/test_route_permissions.py` 0062bac7343dacebb8597407e7630d6ad76218aa58d56514f8a69ded9124ce65; `tests/e2e/test_cli_v12.py` 2af28ff1ba906ef2ce0b3a68a7a3727a8211e5aeaf56508446e86d26d4676496.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/cli.py src/telescreen/cli_v12.py tests/e2e` -> exit 0, 20 files already formatted (log: `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/ruff-format-rerun.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/cli.py src/telescreen/cli_v12.py tests/e2e` -> exit 0, all checks passed (log: `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/ruff-rerun.log`).
- `uv run --frozen ty check src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/cli.py src/telescreen/cli_v12.py tests/e2e` -> exit 0 (log: `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/ty-scoped-rerun.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/e2e` -> exit 0, 30 passed (log: `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/pytest-e2e-rerun.log`).
- `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 792 passed and 5 integration tests skipped as configured (log: `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/pytest-all-rerun.log`).
- `uv run --frozen python tools/typelint.py` -> exit 0, 414 files clean (log: `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/typelint-rerun.log`).
- `uv run --frozen ty check` -> exit 0, all checks passed (log: `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/ty-all-rerun.log`).
- `test "$(wc -l < src/telescreen/wiring.py)" -lt 1000` -> exit 0 (756 lines; log: `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/line-count-final.log`).
**Notes**: Initial route-only diagnostic exposed that this FastAPI version defers
`include_router` as wrappers in `app.routes`; the final test asserts every container router
is included, then walks every concrete `APIRoute` in those routers. The first diagnostic's
output was observed during the session but its log path was reused by a passing rerun, so it
is not cited as a retained failed log.
The first typelint run found four annotation violations, all fixed before final gates
(log: `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/typelint.log`). The first
whole-suite run began before those fixes and failed only the repository typelint assertion
(791 passed, 5 skipped, 1 failed); the stable-tree rerun above passes (log:
`tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-1/pytest-all-rerun.log`). A
preliminary scoped ty run found five `_run_async_command` argument-type errors from
`SimpleNamespace` test values; tests now use `argparse.Namespace` and both final ty runs pass.
No concurrent source behavior was removed. Implementation was performed by Claude in place of Codex Luna
for Step 6 under workflow mode `issue-resolution`; issue reference is workflow input,
continuation 3/session 10 (no GitHub issue number).

### Review repair: 2026-10-11, test-integrity feedback comm-000421
**Finding addressed**: `P11-TI-M01`. Replaced the same-value scope comparison in
`tests/e2e/test_route_permissions.py` with `V1_SCOPE_TABLE`, a literal tuple table keyed by
permission and using explicit `ApiScope` members. Every legacy route now requires a table
entry and compares the scopes granted by the declared permission to the literal set; failure
messages include method, path, actual scopes and expected scopes. The route walk,
external allow-list and permission identity assertions remain in place. The prescribed grep
found no imports/references to scope permission constants or the tautological expression.

**Integration observation**: `git show ebc8db2:src/telescreen/api/common/auth.py` confirms
v1 `Principal.has` accepted only the route's required scope or `ADMIN`. Design section 5.1's
scope columns give read permissions `{READ, WRITE, ADMIN}`, and the requested literal table
encodes those columns. Thus write-only tokens were rejected on read routes in the historical
implementation but are accepted by the current design matrix. This discrepancy is recorded
for serial integration review to reconcile with D12's no-access-change statement; no p06 or
design files were changed in this p11 repair.

**Verification** (attempt-2, final source; complete logs):
- `uv run --frozen pytest -p no:cacheprovider tests/e2e/test_route_permissions.py` -> exit 0,
  1 passed (`tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-2/pytest-route-permissions-final.log`).
- `! grep -nE 'SCOPE_PERMISSIONS|READ_PERMISSIONS|WRITE_PERMISSIONS|ALL_PERMISSIONS|INGEST_PERMISSIONS|_accepted_scopes\\(legacy' tests/e2e/test_route_permissions.py`
  -> exit 0, no matches (recorded in attempt-2 terminal output; source hash below).
- `uv run --frozen ruff format --check --no-cache src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/cli.py src/telescreen/cli_v12.py tests/e2e`
  -> exit 0 (`ruff-format.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/cli.py src/telescreen/cli_v12.py tests/e2e`
  -> exit 0 (`ruff.log`).
- `uv run --frozen ty check src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/cli.py src/telescreen/cli_v12.py tests/e2e`
  -> exit 0 (`ty-scoped.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/e2e` -> exit 0, 30 passed
  (`pytest-e2e.log`).
- `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 792 passed / 5 expected integration
  skips (`pytest-all.log`).
- `uv run --frozen python tools/typelint.py` -> exit 0, 414 files clean (`typelint.log`).
- `uv run --frozen ty check` -> exit 0 (`ty-all.log`).
- `test "$(wc -l < src/telescreen/wiring.py)" -lt 1000` -> exit 0, 756 lines; and
  `test "$(grep -c '^- \\[ \\]' impl-plans/active/v12-p11-wiring-cli.md)" -eq 0` -> exit 0.
- `tests/e2e/test_route_permissions.py` final sha256:
  `761c89cff29b1552485e9dfded8a3c649bbef74758b1a77171058a7a4fdcdb76`.

**Superseded attempt note**: the first shell wrapper used `status` as a variable, which zsh
rejects as read-only after pytest had completed. The pytest log was retained, and the exact
focused test was rerun successfully with a different variable name; only the successful
rerun is final verification evidence.

### Session: 2026-10-11 23:12 JST — integration repair INT-D4-M01B
**Tasks Completed**: Replaced `_validate_files`'s direct `Redactor.has_hits` call with the
accepted p05 `source_has_raw_values` helper. Added CLI tests for the v2 notification-set
example and for free-text email and runtime-built sample credential rejection without value
echo. This resolves INT-D4-M01B in `src/telescreen/cli.py` and `tests/e2e/test_cli_v12.py`.
**Tasks In Progress**: None for this plan. Formal independent review and serial integration
remain downstream workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**:
- `src/telescreen/cli.py` before=ef7f1358077313ec637a8b88ed1960fb137efc8eb59fc778b1f372d3a6c2b759 after recorded in `attempt-6/hashes-after.txt`.
- `tests/e2e/test_cli_v12.py` before=2af28ff1ba906ef2ce0b3a68a7a3727a8211e5aeaf56508446e86d26d4676496 after recorded in `attempt-6/hashes-after.txt`.
- `impl-plans/active/v12-p11-wiring-cli.md` before=afdc6f174fa7e5d8ae1d6bc8b116429c38b3df327bd46a21de9bb3b9435e8731 after recorded in `attempt-6/hashes-after.txt`.
**Verification** (final source; complete logs under `tmp/telescreen-v12-20261011-d4/v12-p11-wiring-cli/attempt-6/`):
- `uv run --frozen ruff format --check --no-cache src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/cli.py src/telescreen/cli_v12.py tests/e2e` -> exit 0 (`ruff-format-final.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/cli.py src/telescreen/cli_v12.py tests/e2e` -> exit 0 (`ruff-final.log`).
- `uv run --frozen ty check src/telescreen/wiring.py src/telescreen/wiring_v12.py src/telescreen/cli.py src/telescreen/cli_v12.py tests/e2e` -> exit 0 (`ty-scoped-final.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/e2e` -> exit 0, 36 passed (`pytest-e2e-final.log`).
- `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 825 passed, 8 expected offline skips (`pytest-all-final.log`).
- `uv run --frozen python tools/typelint.py` -> exit 0, 425 files clean (`typelint-all-final.log`).
- `uv run --frozen ty check` -> exit 0 (`ty-all-final.log`).
- `PYTHONDONTWRITEBYTECODE=1 uv run --frozen telescreen workflow validate examples/workflows/notification-sets.yaml` -> exit 0 (`cli-validate-example-final.log`).
- `test "$(wc -l < src/telescreen/wiring.py)" -lt 1000` -> exit 0; `test "$(grep -c '^- \\[ \\]' impl-plans/active/v12-p11-wiring-cli.md)" -eq 0` -> exit 0 (both terminal outputs retained in the node run).
**Notes**: An earlier attempt-6 Ruff check failed only on import ordering; imports were corrected and the final format/check gates above pass. The focused CLI suite passed 3 tests before the final E2E run. Workflow mode is `issue-resolution`; issue reference is workflow input, continuation 3/session 10, with no GitHub issue number. Implementation ran in place as the Claude implementation node; the plan contract is unchanged.
