# V11-P14 Directory Owner Identities and Team Lead; Search Exposure and Risk

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md section 7 (directory mapping, team lead, `OwnerDirectory`), section 10 (search document); design-docs/specs/design-api-and-ui.md ("v1.1 endpoints", Search paragraph)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p14-directory-search`
- planPath: `impl-plans/active/v11-p14-directory-search.md`
- wave: 2
- dependsOn: `v11-p01-foundation`, `v11-p02-db-migration`
- writePaths:
  - `impl-plans/active/v11-p14-directory-search.md`
  - `src/telescreen/directory`
  - `src/telescreen/search`
  - `tests/directory/test_v11_identities.py` (new; new directory `tests/directory`)
  - `tests/search/test_v11_search.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Directory part:

- `member_identities` maps keyed owner identities to members. A member's email and Slack
  user id derive identity rows automatically, and admins add GitHub logins or extra
  emails (stored keyed only).
- Teams get a lead (the exception approver).
- `DirectoryOwnerResolver` implements the `OwnerDirectory` port pinned by v11-p01.

Search part: issue documents gain `exposure_visibility` and `risk_score`. The Meilisearch
settings make them filterable and sortable, and the in-memory backend supports them.

Imitate:

- `directory/service.py:DirectoryService.create_member` (audit in the caller
  transaction);
- `directory/resolver.py:DirectoryRecipientResolver`;
- `search/documents.py:issue_document`;
- `search/settings.py:INDEX_SETTINGS`.

## Non-goals

- No router changes (v11-p18), no wiring (v11-p19).
- No new roles or scopes. Admin checks stay in routers.
- No storage of raw identity values anywhere. The identity value is keyed with the
  `Fingerprinter` (identity key) through `Fingerprinter.owner_ref` from v11-p01.

## Invariants

- `member_identities.identity_key` stores the hex part of `owner_ref`. Store kind and hex
  separately: `kind` column plus `identity_key` = the 64-hex digest. `member_for_owner`
  splits the `"<kind>:<hex>"` ref the same way.
- New `DirectoryService` fields are appended with defaults:
  `fingerprinter: Fingerprinter | None = None`. When None, identity derivation is skipped
  (v1 wiring keeps working).
- Search: Meilisearch settings change only for the `issues` index; other indexes are
  unchanged.

## Deliverables

1. **`directory/identities.py`** (new) `IdentityService`:
   - `derive_for_member(session, member)` upserts origin `member_email` (kind `email`,
     when an email is set) and `member_slack` (kind `slack_user`, when a slack id is set),
     and deletes stale derived rows of that member.
   - `add(session, handle, kind, value, actor_ref) -> MemberIdentityRow` (origin
     `manual`):
     - a duplicate `(kind, key)` -> `CONFLICT`;
     - an unknown handle -> `NOT_FOUND`;
     - an empty value -> `VALIDATION_ERROR`;
     - audit `identity.added` with kind only.
   - `list(session, handle) -> tuple[IdentityView(id, kind, origin, key_prefix (8 hex),
     created_at), ...]`.
   - `remove(session, handle, identity_id, actor_ref)`: manual rows only; derived rows ->
     `CONFLICT`. Audit `identity.removed`.
2. **`directory/service.py`**:
   - `create_member` calls `derive_for_member` when the fingerprinter is set. v1 has no
     member update operation (`directory/service.py` has only `create_member`,
     `create_team`, `set_team_members` and the list methods), so "on update" in design
     section 7 needs no extra call site in v1.1. Do not add a member update API.
   - `create_team` accepts `lead_handle: str | None = None` (resolved to `lead_member_id`;
     unknown -> `NOT_FOUND`).
   - New `set_team_lead(session, slug, lead_handle | None, actor_ref)` audits
     `team.lead_changed`.
   - Existing callers keep working because the new parameters are defaulted.
3. **`directory/owners.py`** (new) `DirectoryOwnerResolver` implements `OwnerDirectory`:
   - `member_ref` -> `member:<handle>` for active members;
   - `team_ref` -> `team:<slug>`;
   - `team_refs_of` -> refs of every team the member belongs to;
   - `member_for_owner(owner_ref)` looks up `(kind, key)`, then the active member ->
     `member:<handle>`;
   - `team_lead(slug)` -> `member:<handle>` of the lead if active, else None.
   - Unknown or malformed refs return None (no exceptions).
4. **`search/documents.py`**: `issue_document` adds `("exposure_visibility",
   row.exposure_visibility)` and `("risk_score", row.risk_score)`.
5. **`search/settings.py`**: issues filterable adds `exposure_visibility`; sortable adds
   `risk_score`.
6. **`search/memory.py`**: confirm that filters and sorts work for the new fields; add
   int handling in `_sort_value` / `_matches` if needed.
7. **`search/meili.py`**: no logic change expected beyond the settings tuple. If
   `ensure_indexes` caches settings, make sure the new attributes are pushed.

## Pitfalls

- The `IndexGuard` runs on every document. The new fields are enums and ints, so the
  guard must still pass; do not add display text.
- An email identity must be normalized exactly as `Fingerprinter.owner_ref` does. Always
  call `owner_ref(OwnerIdentity(kind, value))` and split it; never re-implement the
  normalization.
- Never echo `value` in errors, audit details or responses.

## Tests

- `tests/directory/test_v11_identities.py` (SQLite harness; a real `Fingerprinter` built
  from a 32-byte test key):
  - creating a member with an email and a slack id -> 2 derived rows;
    `member_for_owner(fp.owner_ref(OwnerIdentity(EMAIL, "Alice@Example.com")))` returns
    `member:alice`;
  - manual github login add / list (key prefix only, no value) / remove;
  - removing a derived row -> `CONFLICT`; a duplicate -> `CONFLICT`;
  - an inactive member -> None;
  - team lead set and `team_lead` returns the lead ref; an unknown lead -> `NOT_FOUND`;
  - `team_refs_of` lists memberships;
  - the DB text of `member_identities` contains no raw input value (scan the row
    strings).
- `tests/search/test_v11_search.py`:
  - an issue document includes both fields;
  - the in-memory backend filter `exposure_visibility=public` and sort
    `risk_score:desc` work;
  - the Meili settings payload (MockTransport capture) includes the new attributes.

## Verification

Conventions section 7 with
`<src>` = `src/telescreen/directory src/telescreen/search` and
`<tests>` = `tests/directory tests/search`.

## Completion Criteria

- [x] Identity service, team lead, `DirectoryOwnerResolver` and the search fields
      implemented.
- [x] Tests pass; scoped gates pass; the typelint grep is empty.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 17:08 JST
**Tasks Completed**: Added keyed derived/manual member identities, team lead management, `DirectoryOwnerResolver`, issue exposure/risk document fields, issue Meilisearch settings, and focused directory/search tests.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None. The in-memory backend already supports scalar equality filters and numeric sorting; regression tests confirm the new fields.
**Hashes**: `src/telescreen/directory/identities.py` before=absent after=b0bc5f4b3aa91be383fb2ba888b493db1d58b61c632aeb5df52805223f558c8c; `src/telescreen/directory/owners.py` before=absent after=49119418c960be50d314e4dd42f4f8afd2466ba9f74c9bb3bbb95960976d9cfb; `src/telescreen/directory/service.py` before=b50c592e3e320a5170d63b8a7e05d45434d3209a2d190ecfde7160dc1d77d402 after=97370aa976eca20214a38e5f645fe91a863fafea9e2290b94dc76a7ee0d13e1c; `src/telescreen/search/documents.py` before=bf499437d0775b79a66fefd2e75b0ab58a42ea0e04748d67eb9254938e19b0f1 after=024481593aa92115f8872cdd1a0bf23c45c9a66aa85141bdfc6a480a2f4dd455; `src/telescreen/search/settings.py` before=bd93186a35d6d388150ef4787a237ed8a67a123816ad89e8570ffd69ca8d2dec after=33c3d29cbc018dcf590cb756ec13d2e16bdf96df612c1b427fcfb773f7704c24; `tests/directory/test_v11_identities.py` before=absent after=91526c7f56b4d39a9a44a744fe6ab2cfa506a2066060be74fb6949d914273be2; `tests/search/test_v11_search.py` before=absent after=8dd5e2c319753103625fd767e5176383c00bb712b28291464d64b8f9c2d103e1.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/directory src/telescreen/search tests/directory tests/search` -> exit 0 (log: `/tmp/telescreen-v11-p14-directory-search-format-final.log`); `uv run --frozen ruff check --no-cache src/telescreen/directory src/telescreen/search tests/directory tests/search` -> exit 0 (log: `/tmp/telescreen-v11-p14-directory-search-ruff-final.log`); `uv run --frozen ty check src/telescreen/directory src/telescreen/search tests/directory tests/search` -> exit 0 (log: `/tmp/telescreen-v11-p14-directory-search-ty-final.log`); `uv run --frozen pytest -p no:cacheprovider tests/directory tests/search` -> exit 0, 11 passed and 1 expected offline integration skip (log: `/tmp/telescreen-v11-p14-directory-search-pytest-final.log`); `test "$(grep -c '^- \[ \]' impl-plans/active/v11-p14-directory-search.md)" -eq 0` -> exit 0; repository typelint reports diagnostics in unrelated concurrent-plan collector/issues/workflow paths (exit 1), while the p14 path filter is empty (gate log: `/tmp/telescreen-v11-p14-directory-search-typelint-gate.log`).
**Notes**: The initial format check and Ruff check exited 1 only because five p14-owned files needed formatting (logs: `/tmp/telescreen-v11-p14-directory-search-format.log`, `/tmp/telescreen-v11-p14-directory-search-ruff.log`). After targeted formatting, both final checks passed. No raw identity values are stored in `member_identities`, returned in identity views, or included in identity audit details. Dependency readiness was satisfied by runtime `acceptedPlanIds` for v11-p01 and v11-p02. Formal review and serial integration remain downstream workflow steps.
