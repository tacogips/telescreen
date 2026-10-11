# V12-P01 Foundation: Vocabulary, Permissions, OIDC Settings, Ports

**Status**: Completed (re-verified in session 9; integration-accepted at fcfb198; archived 2026-10-12)
**Design Reference**: design-docs/specs/design-v1-2-notifications-and-identity.md sections 1, 2, 5.1; design-docs/specs/command.md (OIDC variables); design-docs/user-qa/qa-oidc-sso.md
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p01-foundation`
- planPath: `impl-plans/active/v12-p01-foundation.md`
- wave: 1
- dependsOn: none
- writePaths:
  - `impl-plans/active/v12-p01-foundation.md`
  - `pyproject.toml` (pytest `live` marker only)
  - `src/telescreen/core/vocabulary.py`
  - `src/telescreen/core/config.py`
  - `src/telescreen/core/permissions.py` (new)
  - `src/telescreen/core/oidc_settings.py` (new)
  - `src/telescreen/ports/notifications.py`
  - `src/telescreen/ports/issues.py`
  - `src/telescreen/ports/templates.py` (new)
  - `src/telescreen/ports/identity.py` (new)
  - `tests/support/oidc.py` (new; shared OIDC test helper)
  - `tests/core/v12` (new directory)
  - `tests/ports/v12` (new directory)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Every other v1.2 plan builds on these shared contracts. This plan pins the enums, the
permission matrix, the OIDC settings, and the port DTOs and Protocols, so wave-2 plans can
work in parallel. It contains no behavior beyond pure functions and configuration parsing.

Imitate:

- `src/telescreen/core/vocabulary.py:ApiScope` / `ErrorCode` (StrEnum style, docstrings);
- `src/telescreen/core/config.py:_comma_values`, `_enum_value`, `_boolean`, `_integer`
  (ConfigError messages name the variable, never its value);
- `src/telescreen/ports/directory.py` (frozen DTOs and Protocols with docstrings);
- `src/telescreen/ports/notifications.py:NotificationRequest` (defaulted fields added
  at the end).

## Non-goals

- No dependency change. `pyjwt[crypto]>=2.10` is already in `pyproject.toml`. Do not run
  `uv lock`. The only `pyproject.toml` edit is the `live` marker string.
- No per-file-ignore entries. No change to `tools/typelint.py`.
- The `NotificationTarget` port DTO mentioned in design section 2 is NOT created. The
  workflow DSL (L1) cannot import ports, and executors send one `NotificationRequest` per
  target, so no consumer would exist.
- No router, service or DB work.

## Invariants

- Every new DTO field is defaulted, so all current constructors (`wiring.py`, tests) keep
  working unchanged.
- `vocabulary.py` stays under 1000 lines (it has 642 now). OIDC parsing goes into the new
  `oidc_settings.py`, not into `config.py`.
- Scope permission sets reproduce v1 exactly: `scope_permissions({READ})` is every
  permission a v1 `read`-scope route needed, and so on. The `admin` scope maps to ALL
  permissions, including `ingest.push`.

## Deliverables

1. **`core/vocabulary.py`** additions:
   - `NotificationEvent`: `workflow_notify`, `human_task_assigned`,
     `owner_review_assigned`, `sla_escalated`, `owner_digest`, `exception_requested`,
     `exception_expired`, `verification_inconclusive`, `collector_health_alert`,
     `police_health_alert`.
   - `NotificationStatus.SKIPPED = "skipped"`.
   - `Role`: `admin`, `responder`, `viewer`.
   - `RoleChangeSource`: `manual`, `idp`, `cli`.
   - `MembershipOrigin`: `manual`, `idp`.
   - `IdentityKind.OIDC_SUBJECT = "oidc_subject"` and `IdentityOrigin.OIDC = "oidc"`.
   - `OidcProvisioning`: `link`, `create`.
   - `SignInProvisioning`: `existing`, `linked`, `created`.
   - `PrincipalKind`: `token`, `oidc`.
   - `ExternalAuthKind`: `public`, `provider_signature`, `police_contract`,
     `oidc_sign_in`.
   - `ErrorCode` adds `RECIPIENT_UNREACHABLE`, `UNKNOWN_TEMPLATE`, `UNKNOWN_VARIABLE`,
     `TEMPLATE_EVENT_MISMATCH`, `LAST_ADMIN`, `NO_ROLE`, `NOT_PROVISIONED`,
     `IDENTITY_CONFLICT`, `IDP_UNAVAILABLE`, `INVALID_TOKEN`. Values are the snake_case
     names. Keep the existing `TOKEN_INVALID` (police) untouched.
2. **`core/permissions.py`** (new, L0; imports only `vocabulary`):
   - `class Permission(StrEnum)`. Values: `issues.read`, `issues.write`,
     `issues.resolve_unverified`, `exceptions.read`, `exceptions.write`,
     `exceptions.manage_any`, `workflows.read`, `workflows.write`, `workflows.activate`,
     `runs.read`, `runs.write`, `human_tasks.read`, `human_tasks.complete`,
     `human_tasks.manage_any`, `evidence.read`, `evidence.upload`, `policies.read`,
     `policies.write`, `escalation.read`, `escalation.write`, `directory.read`,
     `directory.write`, `collectors.read`, `collectors.write`, `map_jobs.read`,
     `map_jobs.write`, `police.read`, `police.write`, `audit.read`, `search.read`,
     `chat.use`, `templates.read`, `templates.write`, `users.read`, `users.write`,
     `tokens.read`, `tokens.write`, `auth.self`, `ingest.push`. Member names are
     upper-case with underscores (`ISSUES_READ`).
   - Sets (module constants, `frozenset[Permission]`):
     - `READ_PERMISSIONS` = every `*.read` except `users.read` and `tokens.read`, plus
       `chat.use` and `auth.self`;
     - `WRITE_PERMISSIONS` = `READ_PERMISSIONS` plus `issues.write`, `exceptions.write`,
       `workflows.write`, `runs.write`, `human_tasks.complete`, `evidence.upload`;
     - `ALL_PERMISSIONS` = every member;
     - `MACHINE_PERMISSIONS` = {`ingest.push`};
     - `INGEST_PERMISSIONS` = {`ingest.push`, `auth.self`}.
   - Tables:
     - `SCOPE_PERMISSIONS: tuple[tuple[ApiScope, frozenset[Permission]], ...]`: read ->
       READ, write -> WRITE, admin -> ALL, ingest -> INGEST;
     - `ROLE_PERMISSIONS: tuple[tuple[Role, frozenset[Permission]], ...]`: viewer ->
       READ, responder -> WRITE, admin -> ALL minus MACHINE.
   - Functions (pure):
     - `scope_permissions(scopes: frozenset[ApiScope]) -> frozenset[Permission]` (union);
     - `role_permissions(role: Role | None) -> frozenset[Permission]` (None -> empty);
     - `effective_token_permissions(scopes: frozenset[ApiScope], role: Role | None, bound:
       bool) -> frozenset[Permission]`. Unbound -> scope permissions. Bound ->
       `(scope & role) | (scope & MACHINE)`;
     - `highest_role(roles: Sequence[Role]) -> Role | None` (admin > responder > viewer);
     - `parse_role(value: str) -> Role | None` (`none` and empty -> None; unknown ->
       `TelescreenError(VALIDATION_ERROR, "unknown role")`).
3. **`core/oidc_settings.py`** (new):
   - `OidcRoleMapping(claim_value: str, role: Role)`;
   - `OidcTeamMapping(group: str, team_slug: str)`;
   - `OidcSettings(issuer, client_id, audience, scopes, algorithms: tuple[str, ...],
     leeway_seconds, jwks_cache_seconds, role_claim, role_map, default_role: Role |
     None, group_claim, team_map, provisioning: OidcProvisioning)`, frozen;
   - `load_oidc_settings(env: EnvSource) -> OidcSettings | None`. Returns None when
     `TELESCREEN_OIDC_ISSUER` is unset or empty. Defaults and ranges follow design
     section 2 exactly:
     - algorithms must be a subset of `RS256,RS384,RS512,PS256,PS384,PS512,ES256,ES384`;
       anything else, including `none` and `HS256`, raises `ConfigError`;
     - leeway is 0-300;
     - the issuer must start with `http://` or `https://`;
     - each map entry is `<a>=<b>`; a malformed entry or an unknown role raises
       `ConfigError`.
4. **`core/config.py`**: `TelescreenSettings` gains `oidc: OidcSettings | None = None` and
   `ui_token_login: bool = True`, appended after `risk_weights`. `load_settings` fills
   them (`load_oidc_settings(env)`, `_boolean(env, "UI_TOKEN_LOGIN", True)`).
5. **`ports/notifications.py`**:
   - `NotificationContext`: a frozen dataclass whose fields are all `str | None = None`,
     except `issue_risk_score: int | None` and `task_tier: int | None`. Field names are
     the template variable paths with `.` replaced by `_`: `issue_short_id`,
     `issue_title`, `issue_rule_class`, `issue_severity`, `issue_status`,
     `issue_exposure`, `issue_risk_score`, `issue_location`, `issue_excerpt`,
     `issue_link`, `run_workflow`, `step_name`, `task_title`, `task_due_at`,
     `task_tier`, `task_link`, `assignee_ref`, `digest_summary`, `health_target`,
     `health_condition`, `health_details`.
   - `NotificationRequest` appends: `event: NotificationEvent | None = None`,
     `template_name: str | None = None`, `context: NotificationContext | None = None`,
     `inline: bool = False`, `set_name: str | None = None`, `target_index: int | None =
     None`, `record_unreachable: bool = False`. Field docs:
     - `event` None -> v1 inline path (`subject`/`body` used as given);
     - `event` set and `inline` False -> template rendering;
     - `event` set and `inline` True -> `subject`/`body` used as given, `event` recorded.
   - `NotificationReceipt` appends `skipped: tuple[str, ...] = ()`.
6. **`ports/issues.py`**: `IssueSnapshot` appends `masked_excerpt: str | None = None`.
7. **`ports/templates.py`** (new):
   - `TemplateInfo(name: str, event_kind: NotificationEvent, active_version: int | None)`;
   - `class TemplateCatalog(Protocol)` with `async def lookup(self, session:
     AsyncSession, name: str) -> TemplateInfo | None`.
8. **`ports/identity.py`** (new):
   - `VerifiedToken(issuer: str, subject: str (repr=False), claims_json: str
     (repr=False))`;
   - `ProviderMetadata(authorization_endpoint: str, token_endpoint: str,
     end_session_endpoint: str | None)`;
   - `class BearerVerifier(Protocol)`: `async def verify(self, token: str) ->
     VerifiedToken` (raises `INVALID_TOKEN` / `IDP_UNAVAILABLE`) and `async def
     metadata(self) -> ProviderMetadata`;
   - `MemberAuth(member_id, handle, display_name, role: Role | None, active: bool)`;
   - `SignInRequest(issuer, subject (repr=False), email: str | None (repr=False),
     email_verified: bool, preferred_username: str | None, display_name: str | None,
     role: Role, groups: tuple[str, ...], provisioning: OidcProvisioning, team_map:
     tuple[OidcTeamMapping, ...])`;
   - `SignInResult(member: MemberAuth, provisioning: SignInProvisioning, role_changed:
     bool)`;
   - `class MemberProvisioner(Protocol)`: `async def member_for_identity(self, session,
     issuer: str, subject: str) -> MemberAuth | None` and `async def sign_in(self,
     session, request: SignInRequest) -> SignInResult`.
9. **`pyproject.toml`**: append `"live: needs TELESCREEN_TEST_* live service variables"`
   to `[tool.pytest.ini_options] markers`.
10. **`tests/support/oidc.py`** (new; used by p08, p10, p11 and p12):
    - `TestIdp` dataclass: issuer, audience, one RSA private key (`cryptography`
      `rsa.generate_private_key(public_exponent=65537, key_size=2048)`), its `kid`, and
      an optional second key for rotation;
    - `jwks_json() -> str` (via `jwt.algorithms.RSAAlgorithm.to_jwk`);
    - `mint(claims_json: str, kid: str | None = None, alg: str = "RS256",
      extra_headers_json: str | None = None) -> str` (signs with `jwt.encode`, passing
      dict literals inline);
    - `mock_handler(request: httpx.Request) -> httpx.Response` serving
      `<issuer>/.well-known/openid-configuration` and the JWKS.

    Never store a minted token in a module constant.

## Pitfalls

- `effective_token_permissions` for a bound token whose member has role None must still
  return `ingest.push` when the scopes carry `ingest` or `admin`.
- Do not use `dict`, `Mapping` or `TypedDict` for tables; use tuples of pairs.
- `ConfigError` text must not contain configured values (issuer URLs may contain hosts;
  say "TELESCREEN_OIDC_ISSUER is invalid").
- `tests/support/oidc.py` must pass typelint: annotate everything, and pass mapping
  arguments as inline literals.

## Tests (`tests/core/v12/`, `tests/ports/v12/`)

- `scope_permissions({READ})` contains `issues.read` and does not contain `issues.write`
  or `users.read`.
- `scope_permissions({ADMIN}) == ALL_PERMISSIONS`; `scope_permissions({INGEST}) ==
  {ingest.push, auth.self}`.
- Role sets: viewer == READ, responder == WRITE, admin == ALL - {ingest.push}.
- Effective permissions:
  - bound `{WRITE}` with role viewer -> READ set;
  - bound `{ADMIN}` with role responder -> WRITE plus `ingest.push`, following the
    scope-derived machine grant in the effective-permission formula;
  - bound `{INGEST}` with role None -> {ingest.push};
  - unbound `{READ}` with role None -> READ set.
- `highest_role([viewer, admin]) == admin`; `highest_role([]) is None`.
- `load_oidc_settings`:
  - issuer unset -> None;
  - defaults when only the issuer is set (client_id `telescreen-ui`, audience equals
    client_id, algorithms `("RS256",)`, role map with 3 entries, provisioning `link`);
  - `ALGORITHMS=none` -> ConfigError; `ALGORITHMS=HS256,RS256` -> ConfigError;
  - `LEEWAY_SECONDS=301` -> ConfigError;
  - `ROLE_MAP=x=superuser` -> ConfigError;
  - `TEAM_MAP=security=secops` parses;
  - `DEFAULT_ROLE=viewer` parses.
- `load_settings`: `UI_TOKEN_LOGIN=false` -> False; default True.
- Ports: `NotificationRequest(channel, refs, "s", "b", "a")` still constructs, with event
  None, inline False and record_unreachable False. `IssueSnapshot` construction without
  `masked_excerpt` works. `repr(VerifiedToken(...))` does not contain the subject.
- `tests/support/oidc.py`: a minted token verifies with
  `jwt.decode(token, key=<public>, algorithms=["RS256"], audience=..., issuer=...)` in a
  test; `mock_handler` serves both URLs.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/core src/telescreen/ports` and
`<tests>` = `tests/core/v12 tests/ports/v12 tests/support/oidc.py`. Also run the full
`uv run --frozen pytest -p no:cacheprovider` once and record its exit status and log path.
It must pass, because nothing else changes behavior in wave 1.

## Completion Criteria

- [x] Deliverables 1-10 implemented with docstrings.
- [x] New tests pass; scoped ruff format/check, ty and pytest exit 0; typelint grep empty.
- [x] Full offline pytest exits 0 (log path recorded).
- [x] `git status --porcelain` shows only paths in writePaths for this plan's edits.
- [x] Progress Log entry with hashes and verification lines.

## Progress Log

### Session: 2026-10-11 10:21 JST
**Tasks Completed**: Added the v1.2 vocabulary, permission sets and functions, validated OIDC settings and TelescreenSettings fields, additive notification and issue DTO fields, template and identity ports, live pytest marker, local RSA/JWKS test provider, and focused tests. Corrected the effective-permission example above to follow design D11 and the plan's formula: admin scope plus responder role includes `ingest.push` from the scope-derived machine grant.
**Tasks In Progress**: Full offline pytest gate; no plan-owned implementation remains.
**Blockers**: Full suite currently fails on two changed paths outside this plan's writePaths. `tests/db/test_models.py::test_metadata_has_the_exact_table_contract` omits the concurrent `notification_templates` model (p02 has recorded this as an ownership reconciliation request). `tests/leak/test_ui_fixtures.py::test_every_mock_fixture_is_masked` detects a value in concurrent `src/web/src/api/mock/fixtures/v12.ts` (p03 owns the fixture). Resume after the owning plan or serial join reconciles those paths and rerun the full offline suite.
**Reconciliation requests**: Update `tests/db/test_models.py` to include `notification_templates` and the expected model count; scrub the flagged value in `src/web/src/api/mock/fixtures/v12.ts`. No out-of-scope edits were made.
**Hashes**: `pyproject.toml` before=42b939e0c442981f7d556979b87d8014ad98a8da170e287208142eceb8a416cb after=00043e3fc7b51caf2f0d8176ab7e37db5787b9d73b56bf5b75d6ea45ea2d7d38; `src/telescreen/core/config.py` before=e55a848b3cb581885ee19a24b135bffd024bcc0cabb94a87ef1aafb4a5358693 after=bee951998c0e19be14f1f341cb4d9b028216fd3d7e37eff9d96d168527c257b8; `src/telescreen/core/vocabulary.py` before=6e1c56821cb0481ab72c11a55196a2b0498b80d5b24a89505e3615f9f84f68f0 after=5ff3190d7511ac40c0b74c3dfa0a57dd2a7872686b024fb1663f419c7e36a5b6; `src/telescreen/core/permissions.py` before=absent after=08b66e9f4d34c14c00196c7fc0bff13a071a2ad7593d47e338a8f108d98ae01d; `src/telescreen/core/oidc_settings.py` before=absent after=d63962e77c600bb5aa43cf8d633fc54036c3032756651451c1099ad7c83d060b; `src/telescreen/ports/notifications.py` before=c912e14be2f6e6528d28374653c9784fbe8a860e807ca5f277c827922fb97b72 after=f4a13a582f5ee1411fd76be67d5aa4ae588ac4c4319183ac716b1b03372b16c0; `src/telescreen/ports/issues.py` before=a7c091987d8370136ed7e718dd4062cc51ee6882a5d682b3cf4b1dbe2ff6bafa after=06c7f873f05dac48d431b563771f619cb646aa148e3c5c125d8d5a4f0bf67914; `src/telescreen/ports/templates.py` before=absent after=7292b578a0c82a665c4b2dea5cf3150f7646e550f8a3a82d82018cfad4edc919; `src/telescreen/ports/identity.py` before=absent after=d3b79181527379d5a746b2834e3f226aed16f768f44a6e576bd7c489e474ae3b; `tests/support/oidc.py` before=absent after=dfdfe2022f8bd281cb0199bba1f8f1bb51e41fa4d55642142961a3b0cd98150a; `tests/core/v12/test_permissions_and_oidc_settings.py` before=absent after=0a42d7dc78c5c60e19376caffbb20eebcc73eed95f1b1bc89b70dba4cff73a62; `tests/ports/v12/test_foundation_ports.py` before=absent after=e77ec4eb87ade170c5397975dac1373f62aa2ac3308a3cb523b0a629691488ae; `tests/ports/v12/test_oidc_test_helper.py` before=absent after=0154004cbc1e9252c6fffaa0db81b41d864802122b550ba9cc6de88188b59722; plan before=4ca69e9da8d23798c7f8fc16412b9be62b3947e435f19335bbbe469994e61d95 after=recorded in the plan-local evidence manifest after this update.
**Verification**: Scoped format check -> exit 0 (log: `/tmp/telescreen-v12-p01-foundation-format-final3.log`); scoped Ruff check -> exit 0 (log: `/tmp/telescreen-v12-p01-foundation-ruff-final3.log`); scoped ty check -> exit 0 (log: `/tmp/telescreen-v12-p01-foundation-ty-final3.log`); repository typelint plus scoped grep -> exit 0, no owned-path diagnostics (log: `/tmp/telescreen-v12-p01-foundation-typelint-final3.log`); focused pytest -> exit 0, 15 passed (log: `/tmp/telescreen-v12-p01-foundation-pytest-final3.log`); full offline pytest -> exit 1, 587 passed, 2 failed, 5 skipped (log: `/tmp/telescreen-v12-p01-foundation-pytest-all.log`); live marker check -> exit 0 (log: `/tmp/telescreen-v12-p01-foundation-live-marker.log`); no-dependency/no-typelint-change check -> exit 0 (log: `/tmp/telescreen-v12-p01-foundation-no-dependency-or-typelint-change.log`); git status captured and all p01-owned paths are within writePaths (log: `/tmp/telescreen-v12-p01-foundation-git-status.log`). Initial static/focused failures were corrected and superseded by the passing final scoped checks; evidence is preserved in the earlier `edit-*.md` files and `/tmp/telescreen-v12-p01-foundation-*-final2.log`.
**Notes**: The full-suite failures are classified concurrent p02/p03 issues, not p01 source regressions. The admin-scope/responder plan example contradicted D11's explicit formula; the formula and existing machine-scope behavior were preserved, and the example now states the resulting machine grant.

**Owner resolution (2026-10-11, session 7)**: The only blockers were cross-plan test inventories outside this plan's writePaths. The owner updated `tests/db/test_models.py` (adds `notification_templates`, 34 tables) and `src/web/src/pages/settings/index.test.tsx` (reads the token through `getSession()` instead of the legacy `telescreen.token` key). The UI fixture leak test passes with the final `v12.ts`. Whole-repository gates on the combined wave-1 tree: ruff format/check, typelint, ty, pytest 589 passed / 5 skipped, web typecheck and vitest 165 passed. On redispatch, re-verify on the committed tree, change nothing unless review finds a defect, and report only final passing commands with an empty `priorVerification` (conventions 7a).

### Session: 2026-10-11 (session 9, re-verify, manifest telescreen-v12-20261011-d3)
**Tasks Completed**: Re-verified the committed tree at 8ff064f without source changes. Ticked the full offline pytest criterion, which the owner resolution unblocked.
**Tasks In Progress**: None.
**Blockers**: None.
**Hashes**: No source, test or pyproject.toml change in this session; only this plan file changed (before=f22853de77e2079c8fd9b3a07f959ae950d04f686f62e0ecb399c0d7ee8dd92d).
**Verification** (logs under `tmp/telescreen-v12-20261011-d3/v12-p01-foundation/attempt-1/`): scoped ruff format check -> exit 0 (31 files); scoped ruff check -> exit 0; scoped ty check -> exit 0; focused pytest `tests/core/v12 tests/ports/v12` -> exit 0, 15 passed (`/tmp/telescreen-v12-p01-foundation-pytest.log`); typelint plus scoped grep -> exit 0, 363 files clean (`/tmp/telescreen-v12-p01-foundation-typelint.log`); full offline pytest -> exit 0, 589 passed, 5 skipped (`/tmp/telescreen-v12-p01-foundation-pytest-all.log`); live marker grep -> exit 0; no change to uv.lock, src/web/package.json, src/web/bun.lock or tools/typelint.py -> exit 0.
