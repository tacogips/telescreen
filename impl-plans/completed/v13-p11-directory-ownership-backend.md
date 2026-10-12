# V13-P11 Directory and Ownership Backend: ActorDirectory, Member Edit and Deactivate, Self Tokens, Ownership Rules, Suggested Assignees

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D16, D25-D27), 2, 8.1 (suggested_assignees), 12, 14
**Created**: 2026-10-12
**Last Updated**: 2026-10-13

## Plan Metadata

- planId: `v13-p11-directory-ownership-backend`
- planPath: `impl-plans/active/v13-p11-directory-ownership-backend.md`
- wave: 2 (run 2; Last Updated 2026-10-13 with the run-2 shared-file rules)
- dependsOn: `v13-p01-foundation`, `v13-p02-db`
- writePaths:
  - `impl-plans/active/v13-p11-directory-ownership-backend.md`
  - `src/telescreen/directory`
  - `src/telescreen/issues/routing.py`
  - `src/telescreen/issues/ownership.py`
  - `src/telescreen/api/routers/directory.py`
  - `src/telescreen/api/routers/users.py`
  - `src/telescreen/api/routers/ownership.py`
  - `tests/directory/v13`
  - `tests/issues/v13_ownership`
  - `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`
- sharedPaths: none
- sharedPathNotes:
  - `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`: v13-p01 edited it in wave 1
    (issue module rename). This plan adds `RouteRule` rows only for its own new routes in
    `directory` and `users`. No other wave-2 plan adds routes to a module listed in
    `MODULES`.
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

The organization backend:

- the `ActorDirectory` implementation used for display names everywhere (B4 and B6);
- member edit, deactivate and reactivate;
- self-service tokens limited by role;
- ordered ownership rules that feed owner routing and suggested assignees.

Imitate:

- `directory/roles.py:RoleService.set_role` (the last-admin row lock and audit
  `role.changed`);
- `directory/tokens.py:TokenService.create` and `revoke` (the token is shown once and
  hashed);
- `issues/routing.py:IssueOwnerRouter.route` (the recipient order);
- `api/routers/users.py` (the token routes).

## Non-goals

- No `IssueOwnerRouter` signature change; rule lookup happens inside `route`.
- No re-routing of existing assignments when the rules change (D25).
- No UI. No wiring (v13-p12).

## Invariants

- The last-admin rule also covers deactivation: deactivating the only active admin ->
  409 `last_admin`.
- Deactivation:
  - sets `active = False`;
  - releases every issue that is open (`UNRESOLVED_STATUSES` or `archived`) and assigned
    to that member, through the `IssueRemediation.release_to_team` port with actor
    `system:deactivation`;
  - writes audit `member.deactivated`.

  The existing auth (`api/common/auth.py`) already rejects inactive members. Do not change
  it.
- Self tokens:
  - always bound to the caller;
  - scopes limited by role (viewer `read`; responder `read`, `write`; admin `read`,
    `write`, `admin`); anything else -> 422;
  - only the caller's own tokens are listed and revoked (another member's token id ->
    404).
- Member edit:
  - `email` follows the `email:` recipient rules (one `@`, no whitespace, no
    `,;<>`, at most 254 characters);
  - `slack_user_id` matches `^[UW][A-Z0-9]{8,}$`;
  - the audit holds field names only, never values.
- Ownership pattern and test locator:
  - `Redactor.has_hits` -> 422 `raw_value_detected`, no row, no echo;
  - at most 512 characters;
  - globs: `*` matches within a segment, `**` across segments, `?` matches one character;
    case-sensitive.
- `ActorDirectory.display` returns the same length and order as its input and never
  raises for unknown refs.

## Deliverables

1. **`directory/actors.py`**: add `DirectoryActorDirectory(repository)` implementing
   `ActorDirectory.display` with the ref grammar of v13-p01 (`member:<id|handle>`,
   `team:<id|slug>`, `token:<id>` -> label, `system:*` -> "Telescreen"). Batch the lookups,
   at most 3 queries. Keep `actor_kind_for` unchanged.
2. **`directory/members.py`** (new): `MemberAdminService(repository, roles: RoleService,
   remediation: IssueRemediation | None, audit, clock)` with:
   - `update(session, handle, display_name, email, slack_user_id, actor)`;
   - `deactivate(session, handle, actor)`;
   - `reactivate(session, handle, actor)`.

   Read open issues by assignee through the ORM, read-only, and call the port to
   release.
3. **`directory/tokens.py`**: `TokenService` gains `create_for_member(session,
   member_id, role, label, scopes)`, `list_for_member` and `revoke_for_member`.
4. **`issues/ownership.py`** (new):
   - `OwnershipRuleService(redactor, audit, clock)` with `list`, `create`, `update`,
     `delete`, `reorder(ids)` (all ids exactly once, else 422) and `test(session, source,
     locator) -> RuleMatch(rule_id, team_slug) | None`;
   - `match_rule(rules, source, locator)`: the first enabled rule by `position` whose
     source is null or equal and whose glob matches `display_locator`;
   - `OwnershipAssigneeSuggester` implements `AssigneeSuggester.suggest`. In order:
     1. the owner member (`owners.member_for_owner(owner_ref)`) -> `owner`;
     2. the matching rule team -> `ownership_rule`;
     3. the policy owner team -> `policy_team`.

     Deduplicate the results.
   - Audit `ownership_rule.changed` (id and action).
5. **`issues/routing.py`**: `IssueOwnerRouter.route` inserts `team:<slug>` from the first
   matching rule between the owner member and `decision.owner_team`. A rule whose team is
   missing is skipped with a warning code log.
6. **Routers** (permissions per design section 2):
   - `directory.py`: `PATCH /api/members/{handle}` (`DIRECTORY_WRITE`) and `POST
     /api/members/{handle}/deactivate` and `/reactivate` (`USERS_WRITE`).
   - `users.py`: `GET` and `POST /api/auth/me/tokens` and `POST
     /api/auth/me/tokens/{token_id}/revoke` (`PROFILE_SELF`; a member principal is
     required, otherwise 403). The create response returns `{id, token}` once.
   - New `ownership.py`: `build_ownership_router(deps)`:
     - `GET /api/ownership-rules` and `POST /api/ownership-rules/test`
       (`OWNERSHIP_READ`);
     - `POST /api/ownership-rules`, `PUT` and `DELETE /api/ownership-rules/{rule_id}`, and
       `POST /api/ownership-rules/reorder` (`OWNERSHIP_WRITE`).
   - `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`: add one `RouteRule` per new
     route in `directory` and `users`. `ownership` is a new module, not in `MODULES`, and
     is covered by p12.

## Tests

`tests/directory/v13/`:

- `display`:
  - `("member:<id>", "member:alice", "team:<slug>", "token:<id>", "system:scanner",
    "bogus")` -> kinds member, member, team, token, system, unknown, in order;
  - an inactive member -> `active=False`.
- Member edit:
  - an invalid email or Slack id -> 422;
  - the audit details contain field names and not the email value.
- Deactivate:
  - an admin while another admin exists -> ok, with 2 open issues released (fake
    `IssueRemediation` called twice) and a resolved issue untouched;
  - the last admin -> 409;
  - reactivate -> active.
- Self tokens:
  - a viewer asking for `write` -> 422; a viewer asking for `read` -> created and bound;
  - listing shows only own tokens;
  - revoking another member's token -> 404;
  - the audit has no token value.

`tests/issues/v13_ownership/`:

- `match_rule` order and globs: `github:org/*/config/**` matches
  `github:org/repo/config/app/x.yml`; a source mismatch is skipped; a disabled rule is
  skipped.
- Routing: a new issue without an owner and a matching rule -> assigned to the rule team
  before the policy team; an owner member present -> the owner wins.
- A pattern with a sample secret -> 422 and no row. A test locator with a sample secret ->
  422, and the response does not contain it.
- The suggester returns owner, ownership_rule and policy_team, deduplicated.
- `reorder` with a missing id -> 422.

API: the permission per route (a viewer can test rules but not create; a responder cannot
deactivate). `tests/api/v12_rbac` passes with the new rows.

## Run-2 shared-file rules (design section 15, run 2)

- This plan is the only run-2 plan that adds routes to already registered modules
  (`directory.py`, `users.py`). It is therefore the single run-2 owner of
  `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`, an exact per-module
  inventory: add rows only. `tests/api/v12_rbac/rbac_world.py` is read-only.
- Every new route carries exactly one permission marker, so
  `tests/e2e/test_route_permissions.py` and `tests/e2e/test_api_routes.py` stay read-only
  and must pass unchanged. `ownership.py` is a new, unregistered module, tested by
  building the router directly. Never use `include_router` in source.
- `IssueOwnerRouter.route` keeps its signature and constructor. Add defaulted fields
  only. With no ownership rules, routing output must be byte-identical. Many existing
  tests build it through `tests/e2e/harness.py` and `tests/leak/harness.py`, and these
  stay read-only: `tests/issues/test_issues.py`, `tests/issues/v11_record/**`,
  `tests/e2e/test_flow_ingest.py`, `tests/e2e/test_flow_v11_owner_health.py`,
  `tests/e2e/test_flow_v11_policy.py`, `tests/e2e/test_wiring_v11.py` and
  `tests/leak/test_leak_matrix_v11.py`.
- `TokenService` changes are additive. `tests/directory/v12/**` is read-only.
- No new table, migration or notification kind. `tests/db/test_models.py`,
  `tests/notifications/**` and `tests/leak/**` are read-only.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`.

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/directory src/telescreen/issues/routing.py src/telescreen/issues/ownership.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/users.py src/telescreen/api/routers/ownership.py tests/directory/v13 tests/issues/v13_ownership tests/api/v12_rbac/test_v12_rbac_router_permissions.py
uv run --frozen ruff check --no-cache src/telescreen/directory src/telescreen/issues/routing.py src/telescreen/issues/ownership.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/users.py src/telescreen/api/routers/ownership.py tests/directory/v13 tests/issues/v13_ownership tests/api/v12_rbac/test_v12_rbac_router_permissions.py
uv run --frozen ty check src/telescreen/directory src/telescreen/issues/routing.py src/telescreen/issues/ownership.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/users.py src/telescreen/api/routers/ownership.py tests/directory/v13 tests/issues/v13_ownership tests/api/v12_rbac/test_v12_rbac_router_permissions.py
uv run --frozen python tools/typelint.py > /tmp/telescreen-v13-p11-typelint.log 2>&1; ! grep -E '^(src/telescreen/directory/|src/telescreen/issues/(routing|ownership)[.]py|src/telescreen/api/routers/(directory|users|ownership)[.]py|tests/(directory/v13|issues/v13_ownership)/|tests/api/v12_rbac/test_v12_rbac_router_permissions[.]py)' /tmp/telescreen-v13-p11-typelint.log
uv run --frozen pytest -p no:cacheprovider tests/directory tests/issues/v13_ownership tests/api/v12_rbac tests/api/v12_routers tests/issues/v11_record tests/issues/test_issues.py 2>&1 | tee /tmp/telescreen-v13-p11-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider tests/e2e/test_route_permissions.py tests/e2e/test_api_routes.py tests/e2e/test_flow_ingest.py tests/e2e/test_flow_v11_owner_health.py tests/e2e/test_flow_v11_policy.py tests/e2e/test_flow_v12_identity.py tests/e2e/test_wiring_v11.py tests/leak/test_leak_matrix_v11.py 2>&1 | tee /tmp/telescreen-v13-p11-pytest-shared.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v13-p11-pytest-all.log 2>&1; echo "exit=$?"
```

Expected:

- the scoped checks exit 0, and the typelint grep finds nothing;
- the existing directory, routing, v12 router and shared read-only suites pass;
- every file under `tests/` that changed is in `tests/directory/v13/**`,
  `tests/issues/v13_ownership/**` or `tests/api/v12_rbac/test_v12_rbac_router_permissions.py`.

The full run is informational. A failure caused only by v13-p08's in-progress files is
recorded as such.

## Completion Criteria

- [x] `DirectoryActorDirectory` and `OwnershipAssigneeSuggester` satisfy their ports (ty
  check).
- [x] The last-admin rule covers deactivation; deactivation releases open issues.
- [x] The RBAC table rows exist for every new route in `directory` and `users`.
- [x] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)

### Session: 2026-10-13 01:40 JST
**Tasks Completed**: Implemented batched actor display; member profile edit, last-admin-safe deactivate/reactivate and open-issue release; role-limited self tokens; ownership CRUD, masking, glob matching, suggestions, routing and route permission inventory. Added plan-local regression tests.
**Tasks In Progress**: Serial reconciliation for read-only route inventory tests outside this plan's writePaths.
**Blockers**: `tests/api/v12_routers/test_routers.py` and `tests/e2e/test_route_permissions.py` need updates caused by the new live self-token/member routes; both paths are outside this plan's `writePaths`.
**Reconciliation requests**: Add `tests/api/v12_routers/test_routers.py` and `tests/e2e/test_route_permissions.py` to the serial join write ownership. Update the route count from 16 to 19 and include the new `/api/members/{handle}` PATCH, deactivate/reactivate and `/api/auth/me/tokens` GET/POST/revoke routes in the route and scope inventory. Add `DIRECTORY_WRITE`, `USERS_WRITE`, and `PROFILE_SELF` expected-scope entries as needed. Resume by rerunning the two shared suites below and the plan's combined pytest command.
**Hashes**:
- `src/telescreen/directory/actors.py` before=9ee2d389351cb08a141a1555866d00c54275b5887be4bfac7769539388af4d79 after=0b47eafe612756e146e5c990dedba8d6b4495288e89ebd6417e884250b55a213
- `src/telescreen/directory/members.py` before=absent after=8f298894bc29531539d91127d3edf0f00223205d45c8f57d2d2d5e54606982db
- `src/telescreen/directory/tokens.py` before=97c0d1747f5a17cf4622ed7ef696a1b4aca5b1bae12d3825debc28c908b0ed6f after=bb6ec0e3ec4f74fe1f6c64838300fe96eec55264fd5905563e33d6d5f710c9cd
- `src/telescreen/issues/routing.py` before=9e008088dff50e88bde7c415f2c4e65643ecda92ba3907de5eec49f6edd3331a after=2d19b8aa9383912c90f51e34de7689eef35ce6f2835cca7f93565ed16d3e4286
- `src/telescreen/issues/ownership.py` before=absent after=c93c9759c3f4c55602289579e4510159520dafe3ffd26c76768f8dc32758e0e7
- `src/telescreen/api/routers/directory.py` before=de5bac4979b214eed8af5feb958782e27ee4135f2196b4e01b1d6293769d945a after=40ae1b56539601f85042885964ff4adc4772c666227757fdd11589dcba8a1b73
- `src/telescreen/api/routers/users.py` before=3eb25bcc153358468d10366feb457439c492974f358f53ddda489f04fbdf5b51 after=95e1865bf7cc2856d37b16830ae066a4ac7718b143c2404540b7c1ec5caeb61e
- `src/telescreen/api/routers/ownership.py` before=absent after=7d9ca78496578f37fe0b74271dce7d098ab370aa226db308636014ce98df5027
- `tests/directory/v13/test_directory_members_tokens.py` before=absent after=9258b6a1067884e3907d4e7787375d59c2c3bc5c0be9b73b4acaa5ff1f11ec33
- `tests/issues/v13_ownership/test_ownership_rules.py` before=absent after=11e4930a6b0c3fb83a359fe046e8a68f8ec5b0984dd5a007c769e4156dca0528
- `tests/api/v12_rbac/test_v12_rbac_router_permissions.py` before=f37342de72d55e343ace4d01e3e72ee7425fd88ad47f6ed7f9e61a5cf5a01602 after=e0947513e5e2534591ad98637fc50b5a3b4c7b30fd2b100b470930078fe0daf6
- This plan before=f8b3f5b72c2c30042341b0418c986088477938171d2660937af8bd9b7ba4a007; final after hash is in `tmp/telescreen-v13-20261013-e3/v13-p11-directory-ownership-backend/final-hashes.txt`.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/directory src/telescreen/issues/routing.py src/telescreen/issues/ownership.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/users.py src/telescreen/api/routers/ownership.py tests/directory/v13 tests/issues/v13_ownership tests/api/v12_rbac/test_v12_rbac_router_permissions.py` -> exit 0 (`/tmp/telescreen-v13-p11-ruff-format.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/directory src/telescreen/issues/routing.py src/telescreen/issues/ownership.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/users.py src/telescreen/api/routers/ownership.py tests/directory/v13 tests/issues/v13_ownership tests/api/v12_rbac/test_v12_rbac_router_permissions.py` -> exit 0 (`/tmp/telescreen-v13-p11-ruff-check.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/directory src/telescreen/issues/routing.py src/telescreen/issues/ownership.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/users.py src/telescreen/api/routers/ownership.py tests/directory/v13 tests/issues/v13_ownership tests/api/v12_rbac/test_v12_rbac_router_permissions.py` -> exit 0 (`/tmp/telescreen-v13-p11-ty.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` then the p11-path grep filter -> exit 0, no p11 findings (`/tmp/telescreen-v13-p11-typelint.log`, `/tmp/telescreen-v13-p11-typelint-path-findings.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/directory/v13 tests/issues/v13_ownership tests/api/v12_rbac 2>&1 | tee /tmp/telescreen-v13-p11-plan-local-pytest-final.log` -> exit 0, 19 passed.
- `wc -l src/telescreen/directory/*.py src/telescreen/issues/routing.py src/telescreen/issues/ownership.py src/telescreen/api/routers/directory.py src/telescreen/api/routers/users.py src/telescreen/api/routers/ownership.py > /tmp/telescreen-v13-p11-wc.log; ! awk '$2 != "total" && $1 >= 1000' /tmp/telescreen-v13-p11-wc.log | grep -q .` -> exit 0 (`/tmp/telescreen-v13-p11-wc-status.log`); prohibited-path guard -> exit 0 (`/tmp/telescreen-v13-p11-path-guard.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/directory tests/issues/v13_ownership tests/api/v12_rbac tests/api/v12_routers tests/issues/v11_record tests/issues/test_issues.py 2>&1 | tee /tmp/telescreen-v13-p11-pytest.log` -> exit 1, 78 passed / 1 failed because the read-only router inventory expects 16 instead of 19.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/e2e/test_route_permissions.py tests/e2e/test_api_routes.py tests/e2e/test_flow_ingest.py tests/e2e/test_flow_v11_owner_health.py tests/e2e/test_flow_v11_policy.py tests/e2e/test_flow_v12_identity.py tests/e2e/test_wiring_v11.py tests/leak/test_leak_matrix_v11.py 2>&1 | tee /tmp/telescreen-v13-p11-pytest-shared.log` -> exit 1, 12 passed / 1 failed because `USERS_WRITE` is missing from the read-only scope inventory.
- Superseded focused attempts: `/tmp/telescreen-v13-p11-focused-pytest.log` and `/tmp/telescreen-v13-p11-plan-local-pytest.log`; the current-source plan-local rerun above passes.
**Notes**: `tests/api/v12_rbac/test_v12_rbac_router_permissions.py` passes with the directory and users route rows. Ownership routes are built and asserted directly; they remain unregistered for v13-p12. No dependency, migration, or disallowed path was modified. No Git state-changing command was run.

### Serial reconciliation (session 13, run 2, after the wave-2 join)

- Owner: the serial reconcile-implementations writer (not a Luna worker). Pre and post SHA-256 of every touched file: `tmp/telescreen-v13-20261013-e3/reconcile/pre-hashes.txt`, `tmp/telescreen-v13-20261013-e3/reconcile/post-hashes.txt`.
- Combined tree: `uv run --frozen ruff format --check --no-cache .`, `ruff check --no-cache .`, `python tools/typelint.py` (489 files clean), `ty check` -> exit 0; `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 947 passed / 8 skipped (`tmp/telescreen-v13-20261013-e3/reconcile/py-pytest-all.log`); `uv build` -> exit 0; web install/typecheck/test (223 passed)/build/screenshots -> exit 0, 0 browser errors, no `state-skipped` lines (`tmp/telescreen-v13-20261013-e3/reconcile/web-*.log`).
- Repairs: `tests/api/v12_routers/test_routers.py` route count 16 -> 19 (self-token routes); `tests/e2e/test_route_permissions.py` scope-table rows for USERS_READ/USERS_WRITE/TOKENS_READ/TOKENS_WRITE (admin) and PROFILE_SELF (read/write/admin), matching `scope_permissions`; 12 TL003 findings fixed by typed declarations in `src/telescreen/directory/actors.py` and the two p11 test files (the earlier filtered check missed them because typelint prints absolute paths).
- Combined p11 pytest -> exit 0, 79 passed (`tmp/telescreen-v13-20261013-e3/reconcile/py-pytest-p11-combined.log`); shared p11 pytest -> exit 0, 13 passed (`tmp/telescreen-v13-20261013-e3/reconcile/py-pytest-p11-shared.log`).

### Redispatch: 2026-10-13, INT-E3-P11-01
**Tasks Completed**: Bound self-token creation to the effective scope intersection. Token principals pass their token id; the service rejects missing, revoked or differently bound caller rows and intersects stored caller scopes with role scopes. OIDC callers retain role-only scope limits. Added regression coverage for a read-scoped admin token rejecting `admin` without inserting a row, allowing `read`, and preserving OIDC admin token creation.
**Tasks In Progress**: None in p11. Formal independent review remains downstream.
**Blockers**: None. The prior route-count and scope-inventory reconciliation failures now pass on the combined tree.
**Reconciliation requests**: None.
**Hashes**:
- `src/telescreen/directory/tokens.py` before=bb6ec0e3ec4f74fe1f6c64838300fe96eec55264fd5905563e33d6d5f710c9cd after=bcd7cacc173e31faa037372173b03dcce29e5e0a22fc16d674cc6ac2918bde16
- `src/telescreen/api/routers/users.py` before=95e1865bf7cc2856d37b16830ae066a4ac7718b143c2404540b7c1ec5caeb61e after=00d9849d4e6536b7a50a2dc7909ed437077cd24efbe99684b10cc19794e15bec
- `tests/directory/v13/test_directory_members_tokens.py` before=d9d091290d37a25d6a6468c0fdba4b5c2773f16533ec4ab0cd58ce7c130ddf7a after=18de23f6295fe123e1b45c3233bfbef78b33677f4935d95ff84c29e86d77569b
- Plan before=d02d5ad085b0c27aed91ed70f3ca5659a0ce5d13276641698b559fb2437836d8; final source hashes are recorded in `tmp/telescreen-v13-20261013-e3/v13-p11-directory-ownership-backend/final-hashes.txt`.
**Verification**:
- Scoped `ruff format --check`, `ruff check`, `ty check`, and filtered repository `typelint.py` all exited 0; logs: `/tmp/telescreen-v13-p11-format-redispatch.log`, `/tmp/telescreen-v13-p11-ruff-redispatch.log`, `/tmp/telescreen-v13-p11-ty-redispatch.log`, `/tmp/telescreen-v13-p11-typelint-redispatch-final.log`.
- Token regression: `uv run --frozen pytest -p no:cacheprovider tests/directory/v13/test_directory_members_tokens.py` -> exit 0, 4 passed (`/tmp/telescreen-v13-p11-token-scope-regression.log`).
- Combined p11 suite -> exit 0, 79 passed (`/tmp/telescreen-v13-p11-pytest-redispatch.log`); read-only shared suite -> exit 0, 13 passed (`/tmp/telescreen-v13-p11-pytest-shared-redispatch.log`).
- Full repository `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 948 passed / 8 expected offline skips (`/tmp/telescreen-v13-p11-pytest-all-redispatch.log`).
- Earlier intermediate Ruff format check reported the caller-row guard formatting; formatted the single changed source file and reran the final scoped format gate successfully. No behavior was changed by formatting.
**Notes**: No edits outside p11 writePaths. The serial integration reconciliation updated the route and scope inventories outside this redispatch; their final passing evidence is captured by the combined and shared suite reruns above. No Git state-changing command was run.

### Final verification refresh: 2026-10-13
- Added an ASGI regression through `build_users_router`: the read-scoped admin token's `admin` request returns HTTP 422 and leaves the `ApiTokenRow` count unchanged; its `read` request returns HTTP 201 and stores only `read`. The service test separately verifies OIDC/admin role-based token creation.
- Final focused p11 suite: `uv run --frozen pytest -p no:cacheprovider tests/directory tests/issues/v13_ownership tests/api/v12_rbac tests/api/v12_routers tests/issues/v11_record tests/issues/test_issues.py` -> exit 0, 80 passed (`/tmp/telescreen-v13-p11-pytest-redispatch.log`). Shared suite -> exit 0, 13 passed (`/tmp/telescreen-v13-p11-pytest-shared-redispatch.log`). Full repository suite -> exit 0, 949 passed / 8 offline skips (`/tmp/telescreen-v13-p11-pytest-all-redispatch.log`). The focused token file has 5 passed (`/tmp/telescreen-v13-p11-token-scope-regression.log`).
- Final scoped format, Ruff, ty, and filtered typelint checks exit 0; logs are the redispatch paths listed above. Two intermediate test/lint attempts while authoring the HTTP test exposed the Headers constructor and import order; both were corrected, and the final checks pass.
- Latest `tests/directory/v13/test_directory_members_tokens.py` and plan hashes are recorded in `tmp/telescreen-v13-20261013-e3/v13-p11-directory-ownership-backend/final-hashes.txt`.
