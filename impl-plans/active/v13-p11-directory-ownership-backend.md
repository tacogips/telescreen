# V13-P11 Directory and Ownership Backend: ActorDirectory, Member Edit and Deactivate, Self Tokens, Ownership Rules, Suggested Assignees

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-3-sentry-parity.md sections 1 (D16, D25-D27), 2, 8.1 (suggested_assignees), 12, 14
**Created**: 2026-10-12
**Last Updated**: 2026-10-12

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

- [ ] `DirectoryActorDirectory` and `OwnershipAssigneeSuggester` satisfy their ports (ty
  check).
- [ ] The last-admin rule covers deactivation; deactivation releases open issues.
- [ ] The RBAC table rows exist for every new route in `directory` and `users`.
- [ ] The Progress Log has hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
