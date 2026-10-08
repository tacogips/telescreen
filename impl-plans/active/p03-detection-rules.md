# P03 Detection Types, Rule Detector and Composite Implementation Plan

**Status**: Ready
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

- [ ] All 15 rule ids implemented with validators as specified
- [ ] `Detector`, `ScanItem`, `RawFinding`, `DetectionResult`, `Span`, `PUBLIC_PREFIXES`, `CompositeDetector` exported with the exact names
- [ ] Every sample in `tests/support/samples.py` is detected with the correct class and span
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
