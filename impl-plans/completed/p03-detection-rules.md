# P03 Detection Types, Rule Detector and Composite Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-detectors.md (Types, Rule detector, Composite and configuration)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p03-detection-rules`
- planPath: `impl-plans/active/p03-detection-rules.md`
- wave: 2
- dependsOn: `p01-foundation`
- writePaths:
  - `impl-plans/active/p03-detection-rules.md`
  - `src/telescreen/detection/__init__.py`
  - `src/telescreen/detection/types.py`
  - `src/telescreen/detection/normalize.py`
  - `src/telescreen/detection/rule_detector.py`
  - `src/telescreen/detection/composite.py`
  - `src/telescreen/detection/rules`
  - `tests/detection/rules`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

The rule detector is the deterministic core that powers detection, the `Redactor`, the
IndexGuard and the DLP gate. Its accuracy on credentials and PII (including Japanese
formats) is an acceptance criterion. The `Detector` protocol defined here is the single
batched interface that every backend implements.

## Non-goals

- No LLM, ML or factory code (plan p09).
- No masking or fingerprinting (plan p08).

## Deliverables

### `types.py` (contract)

- `ScanItem(item_id: str, source: str, locator: str, text: str = field(repr=False), content_type: str = "text/plain")`
  (frozen).
- `Span(start: int, end: int)`.
- `RawFinding(rule_id: str, rule_class: RuleClass, span: Span, confidence: float, detector: DetectorKind)`.
- `DetectionResult(item_id: str, findings: tuple[RawFinding, ...], degraded: bool = False)`.
- The Protocol:

```python
class Detector(Protocol):
    kind: DetectorKind
    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]: ...
```

- `check_results(items, results) -> None`: raises `TelescreenError(detector_unavailable)`
  unless there is exactly one result per item, in the same order, with spans inside the
  text. Composite and LLM detectors use it.

### `normalize.py`

- `NormalizedText(text: str, index_map: tuple[int, ...])` (`text` is `repr=False`).
- `normalize(text: str) -> NormalizedText`: per-character NFKC with a map from each
  normalized index to the original index (plus one sentinel entry for the end).
- `to_original(span_in_normalized: Span, normalized: NormalizedText) -> Span`.
- Pitfall: one original character can expand to several normalized characters (for
  example "㍻"). Map every produced character back to its source index; the end offset
  maps to source index + 1.

### `rules/` package

- `rules/catalog.py`:
  - `RuleSpec` (frozen dataclass): `rule_id`, `rule_class`, `pattern: re.Pattern[str]`,
    `value_group: int`, `validator: Callable[[str], bool] | None`,
    `keywords: tuple[str, ...]`, `keyword_window: int`, `confidence: float`.
  - `RULES: tuple[RuleSpec, ...]` with every rule id in the design table:
    `aws_access_key_id`, `aws_secret_access_key`, `github_token`, `slack_token`,
    `google_api_key`, `private_key`, `jwt`, `password_assignment`, `credit_card`,
    `my_number`, `email`, `phone_jp`, `phone_intl`, `address_jp`, `address_en`.
  - `PUBLIC_PREFIXES: tuple[str, ...]` with the closed list from `design-masking.md`
    (masking imports it).
- `rules/validators.py`: `luhn_valid(digits)`, `known_card_iin(digits)` (Visa 4,
  Mastercard 51-55 and 2221-2720, Amex 34/37, JCB 3528-3589, Diners 300-305/36/38,
  Discover 6011/65), `my_number_valid(digits)`, `shannon_entropy(text) -> float`,
  `jwt_header_valid(token)` (base64url-decode the first segment, require a JSON object with
  `alg`), `is_placeholder(value)` (`***`, `${...}`, `<...>`, `changeme`, `example`,
  case-insensitive).
- My Number check digit: for the first 11 digits P(n) (n = 1..11, counted from the RIGHT
  of those 11 digits), weight Q(n) = n + 1 for n <= 6 and n - 5 for n >= 7. remainder =
  sum(P*Q) mod 11. The check digit is 0 when remainder <= 1, else 11 - remainder.

### `rule_detector.py`

- `RuleDetector(rules: tuple[RuleSpec, ...] = RULES)` dataclass, `kind = DetectorKind.RULE`.
- `scan_text(text: str) -> list[RawFinding]` (synchronous): normalize, run every rule on
  the normalized text, validate, check keyword context, map spans back to the original
  text, then resolve overlaps.
- Overlap resolution order: higher confidence first, then high severity, then the longer
  span. Survivors are sorted by start.
- `async def detect_batch(items)`: one result per item using `scan_text`.
- Span semantics (masking depends on this): the span covers only the secret value.
  - `password_assignment`: only the value after `:` or `=`.
  - `private_key`: from the BEGIN line through the END line.
  - `credit_card`: the digits including inner separators.
  - `email`: the full address.

### `composite.py`

- `CompositeDetector(stages: tuple[Detector, ...], mode: DetectorMode)` dataclass; `kind`
  is the first stage's kind. `detect_batch` behaves as follows:
  - `union`: run all stages concurrently. Merge findings: overlapping spans of the same
    class merge into the union span with the max confidence and the detector of the
    strongest finding; overlapping spans of different classes resolve by confidence.
  - `cascade`: stage 1 runs on everything. Later stages see only items with candidates,
    as windows of +-300 characters around each candidate (sub-item id
    `<item_id>#w<n>`), with spans re-based to the original item. Exact-format candidates
    (classes credential, token, private_key, payment_card, national_id with confidence
    >= 0.9) are always kept. Other candidates are kept only if a later stage returns an
    overlapping span.
  - A failing non-first stage (an exception, or `check_results` failure) means: keep the
    previous findings and set `degraded=True` on every affected result. Rule findings are
    never dropped.

## Key Pitfalls

- Never put matched text into exception messages, logs or `RawFinding` (it holds spans
  only).
- Word boundaries: `my_number` must not match inside longer digit runs; a 16-digit card
  must not also yield a 12-digit `my_number` (overlap resolution plus boundaries).
- Full-width digits (`０９０－１２３４－５６７８`) must match `phone_jp` via NFKC, with
  spans in the original string.
- `aws_secret_access_key` requires a keyword within its window AND entropy >= 4.0.
- Use precompiled regexes; avoid catastrophic backtracking (no nested quantifiers on
  overlapping classes).

## Tests (`tests/detection/rules/`)

Use `tests.support.samples` for every positive case; build negatives inline from safe
text.

- Each sample from `all_samples()` embedded in a sentence -> exactly one finding with the
  expected `rule_class`, and its span text (re-sliced in the test) equals the sample
  value (password: the value part only).
- Full-width phone and full-width My Number -> found; span maps to the original
  characters.
- A Luhn-invalid 16-digit number -> no card finding. A wrong My Number check digit -> no
  finding.
- A 40-character random base64 string without an aws keyword -> no
  `aws_secret_access_key`.
- `password=changeme`, `password=${DB_PASS}`, `password: <secret>` -> no finding.
- A JWT whose header does not decode -> no finding.
- A PEM block -> one `private_key` span covering BEGIN..END.
- Japanese address `東京都千代田区丸の内1丁目1-1` (fragment-assembled sample) -> address;
  `〒100-0005 ...` -> address.
- Overlap: a card number also matching phone -> only the card remains.
- Composite union with two fake detectors that return overlapping spans of the same class
  -> one merged span. Cascade: a fake LLM stage confirms one of two medium candidates ->
  only the confirmed one plus the exact-format one remain. A failing stage ->
  `degraded=True` and the rule findings are kept.
- `check_results` with the wrong count -> `TelescreenError(detector_unavailable)`.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/detection tests/detection/rules
uv run --frozen ruff check --no-cache src/telescreen/detection tests/detection/rules
uv run --frozen ty check src/telescreen/detection tests/detection/rules
uv run --frozen pytest -p no:cacheprovider tests/detection/rules 2>&1 | tee /tmp/telescreen-p03-detection-rules-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p03-detection-rules-typelint.log 2>&1; grep -E '^(src/telescreen/detection|tests/detection/rules)' /tmp/telescreen-p03-detection-rules-typelint.log   # prints nothing
```

(p09 adds files under `src/telescreen/detection` in wave 3; at this wave only your files
exist there.)

## Completion Criteria

- [x] All 15 rule ids implemented with validators as specified
- [x] `Detector`, `ScanItem`, `RawFinding`, `DetectionResult`, `Span`, `PUBLIC_PREFIXES`, `CompositeDetector` exported with the exact names
- [x] Every sample in `tests/support/samples.py` is detected with the correct class and span
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 20:05 JST
**Tasks Completed**: Implemented and exported the 15-rule catalog, validators, typed detector contracts, NFKC span mapping, RuleDetector, CompositeDetector union/cascade behavior, and focused sample/edge tests. Dependency p01-foundation was admitted by `acceptedPlanIds`.
**Tasks In Progress**: Independent test-integrity, adversarial and serial integration reviews are downstream workflow steps.
**Blockers**: None for p03 scope.
**Reconciliation requests**: None.
**Hashes**: All p03 source files were absent before implementation. Final source digests are recorded in `tmp/telescreen-v1-20261008-s319/p03-detection-rules/final-source-hashes.sha256`; plan before=5870257767969625e4284098c817e5198ec934efb342684bbde1e7ce6de74e3f; plan after digest is in the same manifest.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/detection/__init__.py src/telescreen/detection/types.py src/telescreen/detection/normalize.py src/telescreen/detection/rule_detector.py src/telescreen/detection/composite.py src/telescreen/detection/rules tests/detection/rules` -> exit 0 (log: `/tmp/telescreen-p03-detection-rules-format-final.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/detection/__init__.py src/telescreen/detection/types.py src/telescreen/detection/normalize.py src/telescreen/detection/rule_detector.py src/telescreen/detection/composite.py src/telescreen/detection/rules tests/detection/rules` -> exit 0 (log: `/tmp/telescreen-p03-detection-rules-ruff-final.log`).
- `uv run --frozen ty check src/telescreen/detection/__init__.py src/telescreen/detection/types.py src/telescreen/detection/normalize.py src/telescreen/detection/rule_detector.py src/telescreen/detection/composite.py src/telescreen/detection/rules tests/detection/rules` -> exit 0 (log: `/tmp/telescreen-p03-detection-rules-ty-final.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/detection/rules` -> exit 0, 10 passed, 0 failed (log: `/tmp/telescreen-p03-detection-rules-pytest-final.log`).
- `uv run --frozen python tools/typelint.py src/telescreen/detection tests/detection/rules` -> exit 0, 9 files clean (log: `/tmp/telescreen-p03-detection-rules-typelint-scoped.log`).
- Whole-repository `uv run --frozen python tools/typelint.py` -> exit 1 with 5 TL003 diagnostics in concurrently owned `src/telescreen/db/models/__init__.py` and `tests/db/*`; no diagnostics in p03 paths (log: `/tmp/telescreen-p03-detection-rules-typelint-all-final.log`). No outside-write-path changes made.
**Notes**: Google API key matching accepts 28-35 suffix characters to include the established synthetic sample while recognizing canonical 35-character suffixes. The p03 source-filtered type gate passed; whole-repository typelint remains with db-owned contributors. Formal reviews and integration acceptance remain downstream.

### Session: 2026-10-08 20:17 JST
**Tasks Completed**: Addressed test-integrity findings P03-TI-001 and P03-TI-002. `RuleDetector.scan_text` now orders candidates highest priority first; the `my_number` regex rejects grouped-digit submatches inside card numbers. Replaced the vacuous overlap check with hyphenated card/phone and grouped card/My Number regressions. Cascade coverage now uses a per-window marker confirmer, two medium candidates beyond index 300, and verifies filtering plus span rebasing. Also corrected the AWS negative fixture to exactly 40 characters and added a keyword-positive control.
**Tasks In Progress**: Independent re-review of the repairs and serial integration remain downstream workflow steps.
**Blockers**: None for p03 scope.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/detection/rule_detector.py` before=6c74d08bbf4798c50917f6a3a123b8b4d1ed6700401d2255e5722be0c4d31c2d after=see `tmp/telescreen-v1-20261008-s319/p03-detection-rules/final-source-hashes.sha256`; `src/telescreen/detection/rules/catalog.py` before=0173f4e256d2186c3c7d2c12a6040f023a2615e252e16bab78421bec3e352e1d after=see manifest; `tests/detection/rules/test_rules.py` before=e7db607985a1d5e8c25159b84b18c0e18bc19e374af1cf6d3465f0221e97bc3c after=see manifest; plan before=ae98fd337e649f22f22832a06020cd11d8bf49ddc9e326a2fdac2b4eeecc509b; plan after digest is in the manifest.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/detection tests/detection/rules` -> exit 0, 9 files already formatted (log: `/tmp/telescreen-p03-detection-rules-format.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/detection tests/detection/rules` -> exit 0 (log: `/tmp/telescreen-p03-detection-rules-ruff.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/detection tests/detection/rules` -> exit 0 (log: `/tmp/telescreen-p03-detection-rules-ty.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/detection/rules` -> exit 0, 10 passed, 0 failed (log: `/tmp/telescreen-p03-detection-rules-pytest.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py src/telescreen/detection tests/detection/rules` -> exit 0, 9 files clean (log: `/tmp/telescreen-p03-detection-rules-typelint-ti-repair.log`).
- Whole-repository `uv run --frozen python tools/typelint.py` with p03-path filtering -> scoped filter exit 0; raw tool exit 1 with six TL003 findings in `src/telescreen/db/models/__init__.py` and `tests/db/*` (log: `/tmp/telescreen-p03-detection-rules-typelint-ti-repair-all.log`).
**Notes**: Test-integrity repair criteria pass. The low-priority Google API fixture/design length difference remains disclosed for serial integration; no p01-owned fixture was changed.

### Session: 2026-10-08 adversarial repairs
**Tasks Completed**: Addressed adversarial findings P03-AR-001 and P03-AR-002. Bounded the email local part to 1..64 characters, restored My Number word boundaries, and raised validated card confidence to 0.985 so cards win overlap resolution. Added four My Number adjacency span regressions and a 100,000-pair long-run scan-time test; retained the shared email sample exact-span coverage and all card assertions.
**Tasks In Progress**: Independent test-integrity and adversarial re-review are downstream workflow steps.
**Blockers**: None for p03 scope.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/detection/rules/catalog.py` before=dbe551ae4aa1bf6d6e0d93ed369e4cfee116e2bf7e81302a460f8e0d197d13ab after=b8709e82f531cf13c0f9860d203de8da250176128fb7a2a100cc2b4a509b28c2; `tests/detection/rules/test_rules.py` before=425c4c649f3955ae13dcd172045d695c4f343c49bdafa108c476ff6ff93a7d97 after=4a10434c42acaa55ebef5b3fb1e8279399c59637b26166852ab58e955d5b4352; plan before=05c758b431c563ebdd177fe998cdbce7264f0063de37ede7cdba8d2b4fb6d179; plan after digest is in `tmp/telescreen-v1-20261008-s319/p03-detection-rules/final-source-hashes.sha256`.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/detection/rules 2>&1 | tee /tmp/telescreen-p03-detection-rules-ar-pytest.log` -> exit 0, 12 passed, including all samples, four adjacent My Number cases, card overlaps, and the long-run timing assertion (0.27s suite).
- `uv run --frozen ruff format --check --no-cache src/telescreen/detection/__init__.py src/telescreen/detection/types.py src/telescreen/detection/normalize.py src/telescreen/detection/rule_detector.py src/telescreen/detection/composite.py src/telescreen/detection/rules tests/detection/rules` -> exit 0 (log: `/tmp/telescreen-p03-detection-rules-ar-format-final.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/detection/__init__.py src/telescreen/detection/types.py src/telescreen/detection/normalize.py src/telescreen/detection/rule_detector.py src/telescreen/detection/composite.py src/telescreen/detection/rules tests/detection/rules` -> exit 0 (log: `/tmp/telescreen-p03-detection-rules-ar-ruff-final.log`).
- `uv run --frozen ty check src/telescreen/detection/__init__.py src/telescreen/detection/types.py src/telescreen/detection/normalize.py src/telescreen/detection/rule_detector.py src/telescreen/detection/composite.py src/telescreen/detection/rules tests/detection/rules` -> exit 0 (log: `/tmp/telescreen-p03-detection-rules-ar-ty-final.log`).
- `uv run --frozen python tools/typelint.py` -> exit 0, 68 files clean (log: `/tmp/telescreen-p03-detection-rules-ar-typelint-raw.log`).
- In-memory regression mutation restoring the unbounded email `+` pattern and scanning `"a." * 100_000` -> the 5s bound was exceeded (56.542s), confirming the performance test rejects the old pattern (log: `/tmp/telescreen-p03-detection-rules-ar-email-mutation.log`).
- `shasum -a 256 -c tmp/telescreen-v1-20261008-s319/p03-detection-rules/final-source-hashes.sha256` -> refreshed after this entry; all 10 entries must verify.
**Notes**: First static-check wrapper attempts returned shell exit 1 because zsh reserves `status`; the wrapped tools had already run, and final reruns using `rc` exited 0. Previous whole-repository typelint diagnostics have cleared on the current shared tree. Google API suffix range {28,35} remains a disclosed p01-fixture/design reconciliation item for serial integration.
