# V11-P01 Foundation: Vocabulary, Settings, Ports, Versioned Fingerprinter

**Status**: Ready
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

- [ ] All symbols of Deliverables 1-6 exist with the stated names and defaults.
- [ ] Scoped ruff format, ruff check and ty pass; the typelint grep prints nothing.
- [ ] Full `pytest` and full `ty check` exit 0, or fail only in the named v11-p02/v11-p03
      paths, with the deselected rerun and the ty grep showing `exit=0`.
- [ ] Progress Log has the hashes and command evidence.

## Progress Log

(append sessions per conventions section 8)
