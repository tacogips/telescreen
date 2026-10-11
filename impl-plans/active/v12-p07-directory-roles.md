# V12-P07 Directory: Roles, Last Admin, OIDC Member Provisioning, Tokens

**Status**: Not Started
**Design Reference**: design-docs/specs/design-v1-2-notifications-and-identity.md sections 1 (D7, D12-D16), 5.3, 5.4, 6.3 steps 3-4; design-docs/specs/design-data-model.md "v1.2 additions"
**Created**: 2026-10-11
**Last Updated**: 2026-10-11

## Plan Metadata

- planId: `v12-p07-directory-roles`
- planPath: `impl-plans/active/v12-p07-directory-roles.md`
- wave: 2
- dependsOn: `v12-p01-foundation`, `v12-p02-db`
- writePaths:
  - `impl-plans/active/v12-p07-directory-roles.md`
  - `src/telescreen/directory`
  - `tests/directory/v12` (new directory)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

The directory owns the `members`, `member_identities`, `team_memberships` and
`api_tokens` tables. v1.2 adds:

- role management with last-admin protection;
- the `MemberProvisioner` port implementation for OIDC: identity lookup, verified-email
  linking or JIT creation, and role and team sync;
- `slack_channel:` recipient resolution;
- token listing and member binding.

Imitate:

- `directory/service.py:DirectoryService.create_member` (audit in the caller
  transaction);
- `directory/identities.py:IdentityService._key_parts` and `derive_for_member` (keyed
  identities via `Fingerprinter.owner_ref(OwnerIdentity(kind, value))`);
- `directory/resolver.py:DirectoryRecipientResolver.resolve`;
- `directory/tokens.py:TokenService`.

## Non-goals

- No JWT handling (v12-p08), no routers (v12-p10), no CLI (v12-p11).
- No member deactivation API. No team creation from IdP groups (unknown slugs are
  ignored).

## Invariants

- The OIDC identity key is `fingerprinter.owner_ref(OwnerIdentity(IdentityKind.OIDC_SUBJECT,
  issuer + "\0" + subject))`, split into kind and hex as `_key_parts` does. The raw
  issuer/sub pair is never stored, logged or audited.
- New service fields are defaulted, so `wiring.py` compiles unchanged.
- `IdentityService.add` (manual) rejects kind `oidc_subject` with `VALIDATION_ERROR`, and
  `remove` treats `oidc` origin rows like derived rows (`CONFLICT`).
- Audit details never contain the email, sub, tokens or claim values.

## Deliverables

1. **`directory/roles.py`** (new) `RoleService(audit, clock, repository)`:
   - `async def set_role(session, handle, role: Role | None, actor_ref, source:
     RoleChangeSource) -> MemberRow`:
     - unknown handle -> `NOT_FOUND`;
     - no change -> return without audit;
     - when the member is currently `admin` and active, the new role is not admin, and
       the source is not `idp`: run `select(MemberRow).where(role == "admin", active
       == True).with_for_update()` and count; if the count is <= 1 -> 409 `LAST_ADMIN`
       "last admin cannot be removed";
     - audit `role.changed` (target member, details `from`, `to`, `source`).
   - `async def list_users(session, idp_role_mapping: bool) -> tuple[UserView, ...]`.
     `UserView(handle, display_name, email, role, idp_managed, active, last_sign_in_at,
     teams: tuple[str, ...])`, where `idp_managed` = the member has an `oidc_subject`
     identity and the flag is true.
2. **`directory/provisioning.py`** (new) `DirectoryProvisioner(fingerprinter, roles:
   RoleService, audit, clock, repository)` implements `MemberProvisioner`:
   - `member_for_identity(session, issuer, subject)`: keyed lookup, then `MemberAuth`,
     or None.
   - `sign_in(session, request)`:
     1. Identity lookup. If found: provisioning `existing`.
     2. If not found and `email_verified` with an email: find active members with a
        case-insensitive email match.
        - Exactly one: if it already has an `oidc_subject` identity -> 409
          `IDENTITY_CONFLICT`; else insert the identity row (origin `oidc`), audit
          `identity.linked` (kind only), provisioning `linked`.
        - None, with `provisioning == create`: create a member (handle = slug of
          `preferred_username` or the email local part: lowercase, `[a-z0-9-]`,
          collapse dashes, at most 64 chars, fallback `user`; suffix `-2`, `-3`, ...
          when taken; display name from `display_name` or the handle; email when
          verified), insert the identity, audit `member.provisioned` and
          `identity.linked`, provisioning `created`.
        - Otherwise -> 403 `NOT_PROVISIONED`.
     3. An inactive member -> 403 `FORBIDDEN`.
     4. Role sync: when `member.role != request.role`, call `roles.set_role(...,
        source=idp, actor_ref="system:auth")`.
     5. Team sync, only when `request.team_map` is non-empty: target teams = mapped
        groups present in `request.groups` whose slug exists. Add missing
        `TeamMembershipRow(origin="idp")`. Delete `origin == "idp"` rows for mapped
        teams that are not targets. Never touch `manual` rows. Audit
        `team_membership.synced` with added and removed counts when either is non-zero.
     6. Set `last_sign_in_at = clock.now()`. Return `SignInResult`.
   - Also call `IdentityService.derive_for_member` for a created member when the
     fingerprinter is set, so its email identity exists (reuse; do not duplicate the
     logic).
3. **`directory/resolver.py`**: `slack_channel:` resolves like `slack:` (one Slack
   channel).
4. **`directory/tokens.py`**:
   - `create(..., member_id, actor_ref: str = "system:tokens")`; the audit uses
     `actor_ref` and actor kind `TOKEN` or `MEMBER` (from the prefix), else `SYSTEM`;
   - `revoke(..., actor_ref: str = "system:tokens")`, same;
   - `list_tokens(session) -> tuple[TokenView(id, label, scopes, member_handle,
     created_at, revoked_at), ...]`, ordered by `created_at`;
   - `member_id_for_handle(session, handle) -> str` (`NOT_FOUND` when missing).
5. **`directory/repository.py`**: the queries the services above need (identity by key,
   members by email (lower), admins for update, memberships by team and origin, tokens
   list). `set_team_members` keeps v1 semantics: it replaces all rows of the team with
   `origin="manual"`.
6. **`directory/__init__.py`**: export `RoleService`, `DirectoryProvisioner`, `UserView`
   and `TokenView`.

## Pitfalls

- Email matching: compare `func.lower(MemberRow.email) == email.lower()`. Two active
  matches -> `NOT_PROVISIONED` (ambiguous; never pick one).
- A created member must not get a role when `request.role` is somehow None. The port
  type forbids None; keep it that way.
- `with_for_update` is a no-op on SQLite. The race is covered by an integration test
  only; the offline test covers the count logic.
- Handle uniqueness: retry the suffix loop by querying, not by catching
  `IntegrityError`.
- Do not log the email or the handle source claim.

## Tests (`tests/directory/v12/`, SQLite harness, real `Fingerprinter` with a 32-byte test key)

- `set_role`:
  - viewer -> admin audited with source manual;
  - the only admin -> responder -> `LAST_ADMIN`;
  - two admins -> demotion allowed;
  - source `idp` demotes the last admin (allowed, audited `source: idp`);
  - unknown handle -> `NOT_FOUND`.
- `sign_in`:
  - `link` with a verified email matching an existing member -> `linked`, an identity
    row whose `identity_key` does not contain the sub or the email, and the role set to
    the request role;
  - a second sign-in -> `existing`;
  - `link` with no match -> `NOT_PROVISIONED`;
  - unverified email -> `NOT_PROVISIONED`;
  - `create` -> member `alice` created; `alice` taken -> `alice-2`;
  - a member that already has another OIDC subject -> `IDENTITY_CONFLICT`;
  - an inactive member -> `FORBIDDEN`;
  - team sync: map `security=secops` adds an idp membership; next sign-in without the
    group removes it; the manual membership is kept; an unknown slug is ignored.
- `member_for_identity` returns the role and the active flag.
- Resolver: `slack_channel:C0123ABCDE` -> slack_channels `("C0123ABCDE",)`.
- Tokens: `list_tokens` shows the member handle; the create audit actor equals the
  passed `actor_ref`; `member_id_for_handle` unknown -> `NOT_FOUND`.
- DB text scan: no table text contains the test sub value or the token value.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/directory` and `<tests>` =
`tests/directory`. Also run `uv run --frozen pytest -p no:cacheprovider tests/directory
tests/api/common` -> exit 0 (informational if p06 is mid-edit; record it).

## Completion Criteria

- [ ] Deliverables 1-6 implemented.
- [ ] `tests/directory` passes; scoped gates pass; typelint grep empty.
- [ ] Progress Log complete.

## Progress Log
