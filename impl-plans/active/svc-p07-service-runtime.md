# SVC-P07 Shared Service Runtime: Directory Replica, Replica Authentication, Parameterized Audit Chains

**Status**: Completed
**Design Reference**: design-docs/specs/design-service-split.md sections 4.1, 6, 7, 8.2, 9 (identity through ports), 11
**Created**: 2026-10-15
**Last Updated**: 2026-10-16

## Plan Metadata

- planId: `svc-p07-service-runtime`
- planPath: `impl-plans/active/svc-p07-service-runtime.md`
- wave: 3
- dependsOn: `svc-p01-foundation`, `svc-p02-db`
- writePaths:
  - `impl-plans/active/svc-p07-service-runtime.md`
  - `src/telescreen/replica`
  - `src/telescreen/audit/service.py`
  - `src/telescreen/audit/chain.py`
  - `src/telescreen/audit/verify.py`
  - `src/telescreen/audit/query.py`
  - `src/telescreen/audit/export.py`
  - `tests/replica`
  - `tests/audit`
- sharedPaths: none
- artifactRoots: none

## Intent and Context

Inventory (svc-p04) and Notification (svc-p05) both need three things:

- a directory replica (apply only-if-newer, lookups);
- a replica-backed authenticator that yields the same `Principal` as Telescreen;
- replica-backed implementations of the existing directory ports;
- an audit chain in their own tables.

This plan builds those once, store-agnostic, so svc-p04 and svc-p05 can run in parallel.
It is a refinement of design section 17: the shared library is pulled out of p04/p05.
svc-p08 records this in the design doc.

Imitate:

- `api/common/auth.py` (`_authenticate_jwt`, `_authenticate_api_token`, `hash_token`);
- `directory/provisioning.py:_identity_key` (keyed OIDC identity: `fingerprinter.owner_ref(OwnerIdentity(IdentityKind.OIDC_SUBJECT, f"{issuer}\0{subject}"))`);
- `directory/resolver.py:DirectoryRecipientResolver`, `directory/actors.py:DirectoryActorDirectory`,
  `directory/owners.py:DirectoryOwnerResolver` and `issues/asset_owner.py:IssueAssetOwnerResolver`
  (the semantics to reproduce);
- `audit/service.py:AuditLogService` and `audit/verify.py`.

## Non-goals

- No HTTP apps, wiring or CLI entry points.
- No change to the main-table directory adapters or to Telescreen authentication.

## Deliverables and File-Level Changes

1. **`replica/store.py`.** `DirectoryReplica(model: DirectoryEntryModel)` (svc-p02 alias)
   provides:
   - `apply(session, entity: DirectoryEntity) -> bool` upserts only if
     `entity.revision > stored.revision`. Absent entities are stored as tombstones
     (`present=False`, `lookup_key` None, `payload_json` = `encode_directory_entity(entity)`
     of the tombstone itself). It returns whether the entity was applied.
   - `lookup_key` per kind:
     - member: handle;
     - team: slug;
     - identity: `f"{payload.identity_kind}:{payload.identity_key}"` (`IdentityEntity`
       uses `identity_kind`; accepted p01 constraint);
     - api_token: token_hash;
     - collector and rules: None.
   - `get(session, kind, id) -> DirectoryEntity | None` and
     `by_lookup(session, kind, key) -> DirectoryEntity | None`. Both ignore tombstones.
   - `members_of_team`, `identities_of_member`, `watermark(session) -> int` (max revision,
     0 when empty).

   Payloads are stored with `encode_directory_entity` and decoded with the svc-p01 codec;
   never as a raw `dict`.
2. **`replica/auth.py`.**
   `ReplicaAuthenticator(sessions, replica, verifier: BearerVerifier | None, fingerprinter: Fingerprinter | None)`
   is callable as `ApiContext.authentication`: `async __call__(request) -> Principal`.
   - A JWT (two dots) is verified with `verifier`. The identity key is computed as above
     and looked up in `by_lookup(identity, f"oidc_subject:{key}")`; match the kind string
     used by `IdentityKind.OIDC_SUBJECT.value` and the `_identity_key` output format. The
     member must be present, active and have a role.
     `Principal(OIDC, None, member_id, role, role_permissions(role))`.
   - An API token is looked up by `by_lookup(api_token, hash_token(token))`, and must not
     be revoked. The role comes from the bound member (active only).
     `effective_token_permissions(scopes, role, bound)`.
   - The error codes and static details are identical to `api/common/auth.py`
     (`unauthorized` "authentication required", `unauthorized` "sign-in required",
     `forbidden` "permission is not granted").
3. **`replica/directory.py`.** Replica implementations of the existing protocols:
   - `ReplicaRecipientResolver(RecipientResolver)`: `resolve`, `is_member_of` and
     `assignee_of` resolve `member:`, `team:` and the mailing-list ref forms exactly as
     `DirectoryRecipientResolver` does. Inactive or absent members -> `recipient_unknown`.
     A team's resolved addresses follow the existing rule.
   - `ReplicaActorDirectory(ActorDirectory)` follows the `ports/directory.py:ActorDirectory`
     docstring rules: `member:` by id then handle, `team:` by id then slug, `token:` as
     the label, `system:` as Telescreen, `collector:<id>` as the collector name or
     "Telescreen", and Unknown otherwise.
   - `ReplicaOwnerDirectory(OwnerDirectory)`: `member_ref`, `team_ref`, `team_refs_of`,
     `member_for_owner` (keyed owner ref -> active member `member:<handle>`) and
     `team_lead`.
   - `ReplicaMembers`: `member(session, member_id) -> MemberEntity | None` for preferences
     and member references (svc-p05).
4. **`replica/rules.py`.** `ReplicaOwnershipRules.match(session, source, locator) ->
   team_ref | None` uses `core.ownership_match.match_rule` over the `ownership_rules`
   entity, then maps the team id to `team:<slug>`.
5. **Audit parameterization.**
   - `audit/chain.py` adds `AuditChainModels(record: AuditRecordModel, head: AuditHeadModel)`
     and three constants: `MAIN_AUDIT_MODELS`, `INVENTORY_AUDIT_MODELS` and
     `NOTIFICATION_AUDIT_MODELS`.
   - `AuditLogService` gains `models: AuditChainModels = MAIN_AUDIT_MODELS`. Verification
     and query functions accept the same parameter with the main default.
   - The hash formula is unchanged (design-data-model "Audit").
   - `audit/export.py`: `export_chain(session, models) -> AsyncIterator[str]` yields one
     JSON line per stored record (stored masked fields only, ordered by seq), then a final
     `{"head_seq":..,"head_hash":..}` line. The function never formats content into errors.
6. **Parity fixture.** `tests/replica/fixture.py` builds one main-store directory (members,
   teams, identities, tokens, rules, collectors) and the equivalent replica, using
   `projections`-free direct construction of `DirectoryEntity` values.

## Key Pitfalls

- The revision guard is strict `>`: an equal revision is a no-op, not an overwrite.
- A tombstone must block an older present entity.
- The parity requirement is behavioral equality, not code sharing. Do not import
  `telescreen.directory` from `telescreen.replica`, because the R7 gates forbid it in the
  services. Tests may import both.
- Default `models` arguments must keep every existing audit call site and test unchanged.
- The identity key needs the fingerprinter. Without one (no keys configured), OIDC
  authentication fails closed (`unauthorized`).
- Import gate (svc-p02, `tests/inventory/core/test_import_boundaries.py`): only
  `replica/` and `audit/chain.py` may import the new inventory models
  (`InventoryDirectoryEntryRow`, `InventoryAuditRecordRow`, `InventoryAuditChainHeadRow`,
  ...). Put the three `*_AUDIT_MODELS` constants in `audit/chain.py`, not in
  `service.py` or `verify.py`. Prefer the svc-p02 `DirectoryEntryModel` alias in
  `replica/`. Run `tests/inventory` in scoped verification to prove this.

## Tests (`tests/replica`, `tests/audit`)

Replica store:

- Apply revision 2, then revision 1 -> the state stays at revision 2.
- A tombstone at revision 3, then present at revision 2 -> it stays absent.

Authentication parity:

- For OIDC (a fake verifier from `tests.support.oidc`), active/inactive/no-role members
  and API tokens (bound, unbound, revoked): the replica `Principal` or error equals
  `api/common/auth.authenticate` on the main fixture.

Directory parity:

- Recipient resolution parity for member/team refs and an unknown ref.
- Actor display parity for member, team, token (label), system, collector (known and
  deleted) and unknown refs.
- `member_for_owner` parity for a keyed owner ref, including an inactive member -> None.
- `ReplicaOwnershipRules.match` parity with `IssueAssetOwnerResolver`'s rule step.

Audit:

- An inventory chain with three appends verifies. Tampering with one stored row ->
  verify reports that seq.
- The export lines contain no sample secret. The main chain behavior is unchanged
  (existing `tests/audit/test_audit.py` passes).

## Verification

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/replica src/telescreen/audit tests/replica tests/audit
uv run --frozen ruff check --no-cache src/telescreen/replica src/telescreen/audit tests/replica tests/audit
uv run --frozen ty check src/telescreen/replica src/telescreen/audit tests/replica tests/audit
uv run --frozen pytest -p no:cacheprovider tests/replica tests/audit tests/directory tests/api tests/inventory 2>&1 | tee /tmp/telescreen-svc-p07-service-runtime-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p07-service-runtime-typelint.log 2>&1; ! grep -E '^(src/telescreen/(replica|audit)|tests/(replica|audit))' /tmp/telescreen-svc-p07-service-runtime-typelint.log
! grep -rn 'telescreen.directory\|telescreen.issues' src/telescreen/replica
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-svc-p07-service-runtime-pytest-all-final.log 2>&1
```

Evidence: scoped formatting, lint, type checks, typelint filter and import guard exit 0;
the assigned suite passes (152 passed, 10 offline integration skips); the full suite passes
(1333 passed, 37 skipped).

## Completion Criteria

- [x] Deliverables 1-6 are present. The parity tests cover auth, recipients, actors,
      owners and rules.
- [x] `grep -rn "telescreen.directory\|telescreen.issues" src/telescreen/replica` prints nothing.
- [x] Verification exits 0. The Progress Log has hashes.

## Progress Log

### Session: 2026-10-16 17:30 JST
**Tasks Completed**: Implemented revisioned directory replica storage, replica authentication and directory adapters, ownership matching, parameterized audit chains and JSONL export; added projection parity, auth fail-closed, audit masking and tamper tests.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: See `tmp/telescreen-svc-20261015-g4/svc-p07-service-runtime/owned-file-sha256.txt` for node-snapshot before hashes and final after hashes.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/replica src/telescreen/audit tests/replica tests/audit` -> exit 0 (log: `/tmp/telescreen-svc-p07-service-runtime-format.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/replica src/telescreen/audit tests/replica tests/audit` -> exit 0 (log: `/tmp/telescreen-svc-p07-service-runtime-ruff.log`).
- `uv run --frozen ty check src/telescreen/replica src/telescreen/audit tests/replica tests/audit` -> exit 0 (log: `/tmp/telescreen-svc-p07-service-runtime-ty.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/replica tests/audit tests/directory tests/api tests/inventory` -> exit 0, 152 passed / 10 skipped (log: `/tmp/telescreen-svc-p07-service-runtime-pytest.log`).
- `uv run --frozen python tools/typelint.py` plus the assigned-path grep guard -> exit 0; no assigned-path violations (log: `/tmp/telescreen-svc-p07-service-runtime-typelint.log`).
- `! grep -rn 'telescreen.directory\\|telescreen.issues' src/telescreen/replica` -> exit 0, no matches (log: `/tmp/telescreen-svc-p07-service-runtime-import-grep.log`).
- `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 1333 passed / 37 skipped (log: `/tmp/telescreen-svc-p07-service-runtime-pytest-all-final.log`).
**Notes**: Earlier shared-tree suite attempts exposed an in-progress sibling projection test marker and seven sibling typelint annotations; those were corrected within sibling-owned paths before the final full-suite run. `tests/audit/test_audit.py` is unchanged.
