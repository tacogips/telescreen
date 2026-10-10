# V11-P01 Foundation: Vocabulary, Settings, Ports, Versioned Fingerprinter

**Status**: Completed (integration-accepted at 809b02d; archived 2026-10-11)
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 2, 3 (settings), 7 (settings), 8 (settings), 9 (key versioning), 10 (risk weights); design-docs/specs/command.md (Environment Variables); design-docs/specs/design-masking.md (Fingerprints)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p01-foundation`
- planPath: `impl-plans/active/v11-p01-foundation.md`
- wave: 1
- dependsOn: none (base: main at 289699e)
- writePaths:
  - `impl-plans/active/v11-p01-foundation.md`
  - `src/telescreen/core/vocabulary.py`
  - `src/telescreen/core/config.py`
  - `src/telescreen/core/risk_weights.py` (new)
  - `src/telescreen/ports`
  - `src/telescreen/masking/fingerprint.py`
  - `src/telescreen/masking/seal.py`
  - `src/telescreen/wiring_builders.py`
  - `src/telescreen/workflow/triggers/service.py`
  - `tests/core/test_v11_foundation.py` (new)
  - `tests/masking/test_v11_fingerprint.py` (new)
  - `tests/ports/test_v11_ports.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first (sections 2, 3, 7, 7a, 8).

## Intent and Context

Every other v1.1 plan imports shared enums, settings, DTOs and port protocols from this
plan, so this plan pins them for all of wave 2. Every new DTO field and constructor
parameter has a default, so all v1 code still type-checks and every existing test passes
unchanged. Plans of later waves rely on that.

## Non-goals

- No database model or migration (v11-p02), no behavior in issues, collectors, police,
  workflow or API.
- No change to `wiring.py` (owned by v11-p19 in wave 4).
- No dependency changes (`pyproject.toml`, `uv.lock` untouched).

## Invariants

- Existing enum values and their string values are unchanged. `IssueResolution.MANUAL` and
  `AUTO` stay.
- With only `TELESCREEN_FINGERPRINT_KEY` set, every fingerprint, resource key and key id
  is byte-identical to v1 (regression test required).
- `RecipientResolver`, `IssueReader`, `IssueRecorder` and `ResourceScanner` protocols do
  not gain methods. New capabilities are new protocols, so existing implementations and
  test fakes keep conforming.

## Deliverables

### 1. `src/telescreen/core/vocabulary.py`

Add exactly the values in design section 2:

- `Severity.CRITICAL = "critical"`;
- `severity_rank(s: Severity) -> int` (critical 3, high 2, medium 1);
- `IssueStatus.ALLOWED`, `PENDING_VERIFICATION`;
- `IssueResolution` gains seven values;
- `IssueHistoryKind` gains ten kinds;
- `HumanTaskKind.OWNER_REVIEW`;
- `EngineTaskKind.VERIFY_RESCAN`, `POLICY_REEVALUATE`, `OWNER_DIGEST`;
- `ErrorCode.SCAN_INCOMPLETE`.

New enums (StrEnum): `Visibility`, `ChannelKind`, `RepoVisibility`, `CoverageState`,
`UnsupportedReason`, `PlacementKind`, `IdentityKind`, `IdentityOrigin`, `PolicyEffect`,
`RemediationAction`, `ExceptionDuration` (values `"7d"`, `"30d"`, `"90d"`,
`"until_regressed"`, `"forever"`; member names `DAYS_7`, `DAYS_30`, `DAYS_90`,
`UNTIL_REGRESSED`, `FOREVER`), `ExceptionStatus`, `HealthCondition`, `HealthStatus`,
`AssetTier`.

Module-level typed tuples:

- `OPEN_STATUSES: tuple[IssueStatus, ...]`;
- `RECONCILED_STATUSES`;
- `CREDENTIAL_CLASSES: tuple[RuleClass, ...]`;
- `PII_CLASSES`.

Also add `visibility_rank(v: Visibility) -> int | None` (public 6 ... private 1; unknown
-> None). Keep the file under 1000 lines (about 600 expected).

### 2. Settings (`src/telescreen/core/config.py`, new `core/risk_weights.py`)

New frozen dataclasses:

- `FingerprintKeyRef(version: int, ref: str = field(repr=False))`;
- `RiskWeights` in `risk_weights.py`, with float fields: `severity_critical=1.0`,
  `severity_high=0.75`, `severity_medium=0.45`, `exposure_public=1.0`,
  `exposure_anyone_with_link=0.9`, `exposure_external_shared=0.8`,
  `exposure_domain=0.6`, `exposure_unknown=0.6`, `exposure_internal=0.45`,
  `exposure_private=0.3`, `asset_cloud_root=1.0`, `asset_deploy_token=0.85`,
  `asset_personal_token=0.75`, `asset_secret=0.65`, `asset_pii_high=0.6`,
  `asset_pii=0.4`, `age_under_1d=0.7`, `age_under_7d=0.8`, `age_under_30d=0.9`,
  `age_over_30d=1.0`.

Parser `parse_risk_weights(raw: str | None) -> RiskWeights`:

- Input is a comma list `group.name=value`, for example
  `severity.critical=1.0,age.over_30d=1.0`.
- Unknown keys, values that are not floats, and values outside `0 < v <= 1` raise
  `ConfigError` with a static message.
- Use `dataclasses.replace` with an explicit if-chain or a tuple of `(key, field_name)`
  pairs. Never use `**`.

New `TelescreenSettings` fields, all defaulted:

| Field | Type and default | Source |
|-------|------------------|--------|
| `fingerprint_keys` | `tuple[FingerprintKeyRef, ...] = ()` | |
| `fingerprint_active_version` | `int = 1` | |
| `internal_domains` | `tuple[str, ...] = ()` | lowercased, stripped |
| `owner_routing` | `bool = False` | |
| `default_team` | `str \| None = None` | |
| `digest_cron` | `str \| None = "0 9 * * 1"` | env present but empty -> None; invalid cron -> ConfigError via `croniter.is_valid` |
| `health_recipients` | `tuple[str, ...] = ()` | |
| `health_failure_threshold` | `int = 3` | |
| `alert_unscannable_public` | `bool = True` | |
| `risk_weights` | `RiskWeights = RiskWeights()` | |

Key loading:

- If both `TELESCREEN_FINGERPRINT_KEY` and `TELESCREEN_FINGERPRINT_KEYS` are set ->
  `ConfigError`.
- `KEYS` format is `1=env:A,2=file:/x`. Split on the first `=` only, because refs contain
  `:`. Version 1 is required. Duplicate or non-positive versions -> ConfigError.
- Legacy `FINGERPRINT_KEY` -> `fingerprint_keys = (FingerprintKeyRef(1, ref),)` and
  `fingerprint_key_ref` unchanged.
- With KEYS set, also set `fingerprint_key_ref` to the version-1 ref, so v1 checks that
  look at it still work.
- `TELESCREEN_FINGERPRINT_ACTIVE_VERSION` defaults to the max version. A version that is
  not configured is a ConfigError.

Env names exactly as in command.md.

### 3. Ports (`src/telescreen/ports/`)

- `collectors.py`:
  - `Exposure`, `Coverage`, `OwnerIdentity` (value `repr=False`) and `PlacementRef`
    (value `repr=False`), exactly as in design section 2;
  - `ResourceContent` gains six defaulted fields, appended at the end: the five of design
    section 2 plus `removed: bool = False`. `removed` is set by collectors when the
    source reports the resource gone (for example `ResourceGoneError` in
    `collectors/rescanner.py`, or removed refs of a change page), and the scanner copies
    it into `ResourceScan.removed`;
  - `RescanKind.INCONCLUSIVE = "inconclusive"`;
  - `RescanResult` gains `coverage: CoverageState | None = None`.
- `findings.py`:
  - `MaskedFinding.fingerprint_key_version: int = 1` (last field);
  - `ResourceScan` gains `exposure: Exposure | None = None`,
    `owner_ref: str | None = None`, `placement_keys: tuple[str, ...] = ()`,
    `modified_at: datetime | None = None`, `removed: bool = False`, appended after
    `observed_at`;
  - new `LocatorExposure(locator: str, exposure: Exposure)`;
  - `ExternalScan.exposures: tuple[LocatorExposure, ...] = ()`.
- `policy.py` (new):
  - `PolicySubject(source: str, rule_class: RuleClass, placement_keys: tuple[str, ...],
    visibility: Visibility, age_reference: datetime)`;
  - `PolicyDecision(effect: PolicyEffect, policy_name: str | None,
    policy_version: int | None, owner_team: str | None, route_to_owner: bool)`;
  - `class PolicyDecider(Protocol): async def decide(self, session: AsyncSession,
    subject: PolicySubject, now: datetime) -> PolicyDecision`;
  - `RiskInputs(severity: Severity, visibility: Visibility,
    asset_tier: AssetTier | None, first_seen: datetime, now: datetime)`;
  - `class RiskScorer(Protocol): def score(self, inputs: RiskInputs) -> int` and
    `def asset_tier(self, rule_id: str, rule_class: RuleClass) -> AssetTier`.
- `issues.py`:
  - `IssueSnapshot` gains `assignee_ref: str | None = None`, `risk_score: int = 0`,
    `visibility: Visibility = Visibility.UNKNOWN` (appended);
  - new `ResourceIssueRef(resource_key, issue_id, short_id)`;
  - new protocol `ResourceIssueReader.open_issues_for_resources(session,
    resource_keys: tuple[str, ...]) -> tuple[ResourceIssueRef, ...]`;
  - new protocol `IssueRemediation` with the three methods of design section 2. Return
    type of `request_verification` is `RemediationReceipt(issue_ids: tuple[str, ...],
    rescans_enqueued: int)`, of `request_exception` is `str` (exception id), of
    `release_to_team` is `None`.
- `directory.py`: new protocol `OwnerDirectory`. All methods are `async` and take
  `session: AsyncSession` first:
  - `member_ref(session, member_id: str) -> str | None` returns `member:<handle>` for an
    active member;
  - `team_ref(session, team_id: str) -> str | None` returns `team:<slug>`;
  - `team_refs_of(session, member_id: str) -> tuple[str, ...]`;
  - `member_for_owner(session, owner_ref: str) -> str | None`;
  - `team_lead(session, team_slug: str) -> str | None` returns `member:<handle>`.

  `team_ref` is a plan-level addition that the issue snapshot (`assignee_ref`) needs.
  Assignment from a returned ref uses the existing `RecipientResolver.assignee_of`.

Export new names from `ports/__init__.py` only if that file already re-exports
(imitate its current style).

### 4. Versioned Fingerprinter (`masking/fingerprint.py`, `masking/seal.py`)

`Fingerprinter` stays a frozen dataclass with the same name and methods. It gains:

- `key_version: int = 1`;
- `identity_secret: bytes | None = field(default=None, repr=False)` (None -> use `key`).

Rules:

- `resource_key` uses the identity key; `fingerprint` and `external` use `key`.
- New methods:
  - `placement_key(kind: PlacementKind, value: str) -> str` returns
    `f"{kind}:{hex}"` with message `"placement\0" + kind + "\0" + value`;
  - `owner_ref(identity: OwnerIdentity) -> str` returns `f"{kind}:{hex}"` with message
    `"identity\0" + kind + "\0" + normalized`. Normalization: email -> NFKC + lower;
    github_login -> lower; slack_user -> unchanged.
- New static constructor `from_versions(keys: tuple[tuple[int, SecretValue], ...],
  active: int) -> Fingerprinter`, which validates that each key has at least 32 bytes.
  `key_id` = first 8 hex characters of sha256 of the ACTIVE key, as in v1.

`Masker.seal` (`masking/seal.py:Masker.seal`) sets
`fingerprint_key_version=self.fingerprinter.key_version` on each `MaskedFinding`.

### 5. `src/telescreen/wiring_builders.py`

`build_primitives` resolves every `settings.fingerprint_keys` ref with `resolve_secret`
and builds `Fingerprinter.from_versions`. `RuntimePrimitives.fingerprint_key` stays the
version-1 secret, because the Drive channel token derives from it. Settings objects
built directly in tests (for example `tests/e2e/test_api_routes.py` builds
`TelescreenSettings(fingerprint_key_ref="env:...")` without `fingerprint_keys`) must keep
working: when `fingerprint_keys` is empty and `fingerprint_key_ref` is set, use
`((1, fingerprint_key_ref),)` with active version 1. Only when both are empty, raise the
existing "TELESCREEN_FINGERPRINT_KEY is required" ConfigError.

### 6. `src/telescreen/workflow/triggers/service.py`

Replace the local `(MEDIUM, HIGH).index()` comparison in `_matches` with `severity_rank`.
No other change in this file.

## Pitfalls

- Do NOT add methods to existing protocols (breaks `DirectoryRecipientResolver`,
  `IssueService` and test fakes in `tests/`).
- Dataclass field order: new fields with defaults go after existing ones. Do not reorder
  existing fields, because callers use positional construction (for example
  `issues/service.py` builds `ResourceScan(...)` positionally).
- Do not log or include secret refs in error messages. Use static ConfigError texts.
- `OwnerIdentity.value` and `PlacementRef.value` must be `field(repr=False)`.
- `severity_rank` must accept every `Severity`. Do not use list index lookups.

## Tests (new files only)

- `tests/core/test_v11_foundation.py`:
  - legacy `FINGERPRINT_KEY` only -> keys `((1, ref),)`, active 1;
  - `KEYS="1=env:A,2=env:B"` -> active 2;
  - both env vars set -> ConfigError;
  - `KEYS` without version 1 -> ConfigError;
  - `ACTIVE_VERSION=3` not configured -> ConfigError;
  - `RISK_WEIGHTS="severity.critical=0.9"` -> field updated;
  - unknown key / `0` / `1.5` -> ConfigError;
  - `DIGEST_CRON=""` -> None; `"bad"` -> ConfigError;
  - `INTERNAL_DOMAINS=" Example.COM ,example.org"` -> `("example.com", "example.org")`;
  - `severity_rank` order;
  - `visibility_rank(UNKNOWN)` is None;
  - status tuples contain exactly the design values.
- `tests/masking/test_v11_fingerprint.py`:
  - single-key Fingerprinter equals the v1 `from_secret` outputs for `fingerprint`,
    `resource_key`, `external` and `key_id`;
  - two versions, active 2 -> `resource_key` equals the version-1 result and `fingerprint`
    differs;
  - `owner_ref` for `"A@Example.com"` equals that for `"a@example.com"`;
  - `repr(OwnerIdentity(...))` and `repr(PlacementRef(...))` contain no value;
  - `Masker.seal` output carries `key_version`.
- `tests/ports/test_v11_ports.py`:
  - v1-style positional construction of `ResourceScan`, `MaskedFinding`,
    `ResourceContent` and `IssueSnapshot` still works and yields the defaults;
  - the new protocols are importable.
- Trigger: add one case to `tests/core/test_v11_foundation.py` calling
  `telescreen.workflow.triggers.service._matches` with `min_severity=HIGH` and a critical
  event -> True.

## Verification

Use conventions section 7 with
`<src>` = `src/telescreen/core src/telescreen/ports src/telescreen/masking src/telescreen/wiring_builders.py src/telescreen/workflow/triggers`
and `<tests>` = `tests/core tests/masking tests/ports`. Then run the whole offline suite
once, which must stay green because everything is additive:
`uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v11-p01-foundation-pytest-all.log 2>&1; echo "exit=$?"`
and `uv run --frozen ty check > /tmp/telescreen-v11-p01-foundation-ty-all.log 2>&1; echo "exit=$?"`.
Both must show `exit=0`, with exactly one tolerance. The concurrent wave-1 plans v11-p02
and v11-p03 may still be editing their files. The only tolerated failures are tests that
exercise or live in their owned paths, and ty diagnostics located in them:

- v11-p02: `src/telescreen/db/models`, `src/telescreen/db/migrations/versions/0002_v1_1.py`,
  `tests/db/test_v11_migration.py`;
- v11-p03: `src/telescreen/contracts/police/v1`, `src/telescreen_police_sdk`,
  `tests/contract`, `tests/police_sdk/test_v11_exposures.py`.

If a tolerated failure occurs: record the failing test ids (pytest) or diagnostic file
paths (ty) in the Progress Log as informational, then prove nothing else fails:

- pytest: rerun with one `--deselect <test id>` per tolerated id, to
  `/tmp/telescreen-v11-p01-foundation-pytest-all-deselected.log`; it must show `exit=0`.
- ty: `! grep -E '^ *--> ' /tmp/telescreen-v11-p01-foundation-ty-all.log | grep -v -E '(src/telescreen/db/models/|src/telescreen/db/migrations/versions/0002_v1_1\.py|tests/db/test_v11_migration\.py|src/telescreen/contracts/police/v1/|src/telescreen_police_sdk/|tests/contract/|tests/police_sdk/test_v11_exposures\.py)'; echo "exit=$?"`
  must show `exit=0` (no diagnostic outside the tolerated paths).

Any failure or diagnostic in a path this plan owns, or in any other v1 path, blocks
completion. Per conventions 7a, a run with tolerated failures goes only in the Progress
Log; only the passing deselected rerun and the passing grep go in the step
`verification` list.

## Completion Criteria

- [x] All symbols of Deliverables 1-6 exist with the stated names and defaults.
- [x] Scoped ruff format, ruff check and ty pass; the typelint grep prints nothing.
- [x] Full `pytest` and full `ty check` exit 0, or fail only in the named v11-p02/v11-p03
      paths, with the deselected rerun and the ty grep showing `exit=0`.
- [x] Progress Log has the hashes and command evidence.

## Progress Log

### Session: 2026-10-10 16:36 JST
**Tasks Completed**: Implemented Deliverables 1-6: shared vocabulary and ranks, typed risk weights/settings/key loading, additive port DTOs and protocols, versioned fingerprinter and masker key version, primitive builder compatibility fallback, trigger severity rank fix, and new foundation/fingerprint/ports tests.
**Tasks In Progress**: Full offline pytest gate requires reconciliation and a final serial rerun after concurrent sibling work settles.
**Blockers**: `tests/db/test_models.py::test_metadata_has_the_exact_table_contract` fails because v11-p02 adds six v1.1 tables, but `tests/db/test_models.py` is not in v11-p02's declared writePaths or the v11-p01 tolerated-path list. The full suite and the required one-test-deselected rerun also hit `tests/workflow/mapreduce/test_mapreduce.py::test_dispatch_caps_ten_thousand_shards_at_thirty_two`'s 60-second guard (71.76 seconds in the final deselected run); the test passes alone in 51.77 seconds. Neither path is writable under this plan. Resume after serial sibling integration addresses or authorizes the metadata contract test and the full suite passes on a stable tree.
**Reconciliation requests**: Ask the v11-p02 owner/serial join to reconcile `tests/db/test_models.py::test_metadata_has_the_exact_table_contract` with the new metadata tables, or amend its declared writePaths. Do not change that test in v11-p01. Re-run the full offline suite after sibling fanout has settled to verify the mapreduce timing guard without concurrent fanout load.
**Hashes**: `src/telescreen/core/vocabulary.py` before=78581fad5e415f6d97b0cc3a7afde62525bcdc9db72fd8d064764bd15a1b5afd after=6e1c56821cb0481ab72c11a55196a2b0498b80d5b24a89505e3615f9f84f68f0; `src/telescreen/core/config.py` before=c83f9b420aaeaaf803f53683464b4db756e25609a738cd7a378c40576c243088 after=e55a848b3cb581885ee19a24b135bffd024bcc0cabb94a87ef1aafb4a5358693; `src/telescreen/core/risk_weights.py` before=absent after=d8b48f61c8549d16dd29ba59e08738f2b5d400249a22f8dd7b1c9bfd68a3770a; `src/telescreen/ports/collectors.py` before=a326622fbf38796e097109c373286d0b5b63f9b687b9a8e55762074d9930e796 after=acd110c81dcc95eca2249cf3d4fbe9d1c56311554a639b8403b3d8f2e1a2da30; `src/telescreen/ports/findings.py` before=09876540ae9876e41fb877979bcffe53021cdd3b93b5ef594d331f5764d2146a after=63e08a838388c2ac85701d466e3f0e7e96beb40141156667babdf11c0f7a6e58; `src/telescreen/ports/issues.py` before=8392b34d32579c14b16dd8c0977ac21cfb0efde8d721464dd71f5d3c1facd958 after=a7c091987d8370136ed7e718dd4062cc51ee6882a5d682b3cf4b1dbe2ff6bafa; `src/telescreen/ports/directory.py` before=d1f4319f3e0bed6317fe2569822a10c563ae6e4c20705145e91861abda684214 after=2e254be56683e7886d1c356b5895ea320648a071713b0ad41f9a517bd994af79; `src/telescreen/ports/policy.py` before=absent after=2aabdc710a42460dc71123b5c957320416c36fa3409f92c02feab0d09e4b1b12; `src/telescreen/masking/fingerprint.py` before=4938cfcbcd05b7052c76c68f88f0cc45499bde40ab8fed1f2d8d8603d983e3ba after=17efd56c11c4a1e5686271d5aac408f3bcad2455cee2c553deb8d7fc83113303; `src/telescreen/masking/seal.py` before=024fe600e6faeaea3e57a7f286ac48d2d46234f3ca1c44313b7e589100f23371 after=dc7d18ca32f56c88d6fcdbaca2a0127151224f6fd7f4fc3b1559dec61457b9a8; `src/telescreen/wiring_builders.py` before=a31604055b00189b07869c29d2114b5e6dda79a73a105926393c84a1cef40584 after=6b1215333728c9d96fc657c1b7ab636692896e40cdef47dbc42426c17e2f7053; `src/telescreen/workflow/triggers/service.py` before=e8393d33330c985bde1e81b495d2ad614586715485f9b7c322f4a54e62ee0da1 after=abcb7f04e4b21bdd93eca76bb0156813033cea324c8ea515b3540ded1f90bcbb; `tests/core/test_v11_foundation.py` before=absent after=cc9e5af0e76e5237821cb5dea7da6f12fdca6136bf3b813ec68749dc5b4b3234; `tests/masking/test_v11_fingerprint.py` before=absent after=fde691a3eece5876d0ff0291949a98157f8abef21b710c2ff889a45d2facaab6; `tests/ports/test_v11_ports.py` before=absent after=de1989aa0785e351af78c0f4501a1977a821af97e665057f744bb6a0c948f6db. Plan file before=a5951c8f4114001f3eb307d7870869ebf0651d84e0765bf99cb015b536d293ba4; its final hash is recorded in `tmp/telescreen-v11-20261010-c1/v11-p01-foundation/final-sha256.txt` to avoid self-reference.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/core src/telescreen/ports src/telescreen/masking src/telescreen/wiring_builders.py src/telescreen/workflow/triggers tests/core tests/masking tests/ports` -> exit 0 (log: `/tmp/telescreen-v11-p01-foundation-ruff-format-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/core src/telescreen/ports src/telescreen/masking src/telescreen/wiring_builders.py src/telescreen/workflow/triggers tests/core tests/masking tests/ports` -> exit 0 (log: `/tmp/telescreen-v11-p01-foundation-ruff-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/core src/telescreen/ports src/telescreen/masking src/telescreen/wiring_builders.py src/telescreen/workflow/triggers tests/core tests/masking tests/ports` -> exit 0 (log: `/tmp/telescreen-v11-p01-foundation-ty-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/core tests/masking tests/ports` -> exit 0; 52 passed (log: `/tmp/telescreen-v11-p01-foundation-pytest-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` plus absolute-path-aware scoped grep -> tool exit 0, 299 files clean, no scoped diagnostic (log: `/tmp/telescreen-v11-p01-foundation-typelint-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check` -> exit 0 (log: `/tmp/telescreen-v11-p01-foundation-ty-all-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider` -> exit 1; 418 passed, 4 skipped, 2 failed: DB metadata contract (p02 change, path authorization unresolved) and mapreduce timing guard (log: `/tmp/telescreen-v11-p01-foundation-pytest-all-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider --deselect tests/db/test_models.py::test_metadata_has_the_exact_table_contract` -> exit 1; 418 passed, 4 skipped, 1 failed, 1 deselected; the remaining failure is the mapreduce timing guard (log: `/tmp/telescreen-v11-p01-foundation-pytest-all-deselected-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/workflow/mapreduce/test_mapreduce.py::test_dispatch_caps_ten_thousand_shards_at_thirty_two` -> exit 0; 1 passed in 51.77s (log: `/tmp/telescreen-v11-p01-foundation-mapreduce-isolated.log`).
**Notes**: An earlier deselected full-suite run passed 417 tests before the final source/test edits; it is superseded and is not final-source evidence. A later deselected run had an intermittent audit assertion; the isolated audit test passed. No edits were made outside this plan's writePaths. Other native fanout changes are present in p02/p03/p04 and must be preserved.

**Owner resolution (2026-10-10)**: The full-pytest failure was `tests/db/test_models.py::test_metadata_has_the_exact_table_contract` (v11-p02's six new tables). The owner updated that test (EXPECTED_TABLES + count 33); `tests/db` passes. Re-run the gates and report per conventions 7a.

### Redispatch: 2026-10-10 (INT-V11-W1-P01)
**Tasks Completed**: Re-ran the full offline pytest and full ty checks on the current combined tree. The metadata table contract is reconciled and the mapreduce timing guard passed on this run. All p01 implementation criteria are complete; independent progress-gate, test-integrity and adversarial reviews remain downstream.
**Blockers**: None.
**Addressed finding**: `INT-V11-W1-P01`. The aggregate completion checkbox is now ticked after both required commands exited 0. Full pytest reported 420 passed and 4 skipped; full ty reported all checks passed.
**Current p01 source hashes**: `src/telescreen/core/vocabulary.py` 6e1c56821cb0481ab72c11a55196a2b0498b80d5b24a89505e3615f9f84f68f0; `src/telescreen/core/config.py` e55a848b3cb581885ee19a24b135bffd024bcc0cabb94a87ef1aafb4a5358693; `src/telescreen/core/risk_weights.py` d8b48f61c8549d16dd29ba59e08738f2b5d400249a22f8dd7b1c9bfd68a3770a; `src/telescreen/ports/collectors.py` acd110c81dcc95eca2249cf3d4fbe9d1c56311554a639b8403b3d8f2e1a2da30; `src/telescreen/ports/findings.py` 63e08a838388c2ac85701d466e3f0e7e96beb40141156667babdf11c0f7a6e58; `src/telescreen/ports/issues.py` a7c091987d8370136ed7e718dd4062cc51ee6882a5d682b3cf4b1dbe2ff6bafa; `src/telescreen/ports/directory.py` 2e254be56683e7886d1c356b5895ea320648a071713b0ad41f9a517bd994af79; `src/telescreen/ports/policy.py` 2aabdc710a42460dc71123b5c957320416c36fa3409f92c02feab0d09e4b1b12; `src/telescreen/masking/fingerprint.py` 17efd56c11c4a1e5686271d5aac408f3bcad2455cee2c553deb8d7fc83113303; `src/telescreen/masking/seal.py` dc7d18ca32f56c88d6fcdbaca2a0127151224f6fd7f4fc3b1559dec61457b9a8; `src/telescreen/wiring_builders.py` 6b1215333728c9d96fc657c1b7ab636692896e40cdef47dbc42426c17e2f7053; `src/telescreen/workflow/triggers/service.py` abcb7f04e4b21bdd93eca76bb0156813033cea324c8ea515b3540ded1f90bcbb; `tests/core/test_v11_foundation.py` cc9e5af0e76e5237821cb5dea7da6f12fdca6136bf3b813ec68749dc5b4b3234; `tests/masking/test_v11_fingerprint.py` fde691a3eece5876d0ff0291949a98157f8abef21b710c2ff889a45d2facaab6; `tests/ports/test_v11_ports.py` de1989aa0785e351af78c0f4501a1977a821af97e665057f744bb6a0c948f6db.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider` -> exit 0; 420 passed, 4 skipped (log: `/tmp/telescreen-v11-p01-foundation-pytest-all-redispatch.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check` -> exit 0; all checks passed (log: `/tmp/telescreen-v11-p01-foundation-ty-all-redispatch.log`).
**Final manifest gates**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/core src/telescreen/ports src/telescreen/masking src/telescreen/wiring_builders.py src/telescreen/workflow/triggers tests/core tests/masking tests/ports` -> exit 0; 41 files already formatted (log: `/tmp/telescreen-v11-p01-foundation-ruff-format-redispatch.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/core src/telescreen/ports src/telescreen/masking src/telescreen/wiring_builders.py src/telescreen/workflow/triggers tests/core tests/masking tests/ports` -> exit 0; all checks passed (log: `/tmp/telescreen-v11-p01-foundation-ruff-redispatch.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/core src/telescreen/ports src/telescreen/masking src/telescreen/wiring_builders.py src/telescreen/workflow/triggers tests/core tests/masking tests/ports` -> exit 0; all checks passed (log: `/tmp/telescreen-v11-p01-foundation-ty-scoped-redispatch.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/core tests/masking tests/ports` -> exit 0; 52 passed, 0 failed (log: `/tmp/telescreen-v11-p01-foundation-pytest-redispatch-scoped.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` plus the scoped grep from the manifest -> exit 0; 299 files clean and no scoped diagnostics (log: `/tmp/telescreen-v11-p01-foundation-typelint-redispatch.log`).
- `test "$(grep -c '^- \\[ \\]' impl-plans/active/v11-p01-foundation.md)" -eq 0` -> exit 0; no unchecked completion criteria.
