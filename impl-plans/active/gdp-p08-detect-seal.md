# GDP-P08 Masking, Snippet Sealing, Redactor, Fingerprints and the detect.Engine

**Status**: Ready
**Run 2**: Re-dispatched 2026-10-30 under `impl-plans/active/telescreen-gdp-20261030-h2-dispatch.json` (supersedes h1; same tasks, wave and writePaths). Evidence goes under `tmp/telescreen-gdp-20261030-h2/gdp-p08-detect-seal/attempt-<n>/`. Go caches follow the 2026-10-30 Go ruling in `impl-plans/conventions.md` section 7: never an artifact root, never a blocker.
**Design Reference**: design-docs/specs/design-go-gdrive-police.md sections 8.3, 9.3, 9.4, 9.5
**Created**: 2026-10-30
**Last Updated**: 2026-10-30

## Plan Metadata

- planId: `gdp-p08-detect-seal`
- planPath: `impl-plans/active/gdp-p08-detect-seal.md`
- wave: 3
- dependsOn: `gdp-p05-detect-core`, `gdp-p02-parity-fixtures`
- writePaths:
  - `impl-plans/active/gdp-p08-detect-seal.md`
  - `go/internal/detect/mask.go`
  - `go/internal/detect/redact.go`
  - `go/internal/detect/seal.go`
  - `go/internal/detect/fingerprint.go`
  - `go/internal/detect/engine.go`
  - `go/internal/detect/seal_parity_test.go`
  - `go/internal/detect/engine_test.go`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` sections 2, 7 and 8. Do not edit gdp-p05 files
(`normalize.go`, `validators.go`, `rules*.go`, `detector.go`, `core_test.go`,
`detector_parity_test.go`) or `api.go`. If you find a detector parity bug, write a
Reconciliation request for gdp-p05 with the failing fixture index.

## Intent and Context

Produce the exact `masked_snippet`, `location` and `external_fingerprint` strings that
the Python sealing path would produce, so every string the worker sends passes the
Bureau `Redactor`. The Python references:

- `src/telescreen/masking/mask.py:mask_value`, plus `PUBLIC_PREFIXES` from
  `detection/rules/catalog.py`;
- `src/telescreen/masking/seal.py:Masker._snippet`, `seal`, `display_locator`;
- `src/telescreen/masking/redact.py:Redactor.redact`, `has_hits`;
- `src/telescreen/masking/fingerprint.py:Fingerprinter.normalize_for_fingerprint`;
- `src/telescreen_police_sdk/masking.py:external_fingerprint`, `item_fingerprint`;
- the clean-derivation rule in design 9.4.

## Non-goals

- Bureau-side fingerprints (`Fingerprinter.fingerprint`, `external`, `resource_key`).
  The Bureau re-keys these.
- Any change to the detector.

## Deliverables (exported API is a contract for gdp-p10)

- `func MaskValue(value []rune, ruleID string, class RuleClass, t *pytext.Tables) string`,
  an exact port of `mask_value`:
  - The longest public prefix comes first.
  - Star counts are `min(n, 16)`.
  - `[len]` is a code-point length.
  - The PEM header uses `t.SplitLines` and then `t.Strip`, and must fullmatch
    `-----BEGIN [A-Z0-9 ]+-----`.
  - The card mask is `**** **** **** ` plus the last 4 of the decimal digits
    (`DecimalValue` chars kept as their original runes).
  - E-mail is `local[:1] + "**⁠*@" + domain`, using `rsplit("@", 1)`.
  - Phone uses `t.IsDigit`.
  - Address is `[ADDRESS REDACTED]`. The default is `********[len]`.
- `type Redactor struct` with `func NewRedactor(s Scanner, t *pytext.Tables) *Redactor`,
  `Redact(text string) string` and `HasHits(text string) bool`. These port `redact.py`:
  replace spans in reverse order using the original runes.
- `func ExternalFingerprint(value string, key []byte) string` and
  `func ItemFingerprint(itemKey string, key []byte) string`: hex HMAC-SHA256 of the
  UTF-8 bytes.
- `func normalizeForFingerprint(raw string, class RuleClass, t *pytext.Tables) string`:
  `unorm.NFKC` of the whole string. For `payment_card`, `national_id` and `phone`,
  remove `" "` and `"-"`. For `email`, apply `t.Lower`.
- `func findingFingerprint(...) (string, bool)`:
  - `message = "v1\x00" + ruleID + "\x00" + normalized`.
  - `fp0 = ExternalFingerprint(message, key)`.
  - If the Redactor flags `fp0`, try `n = 1..16` with `message + "\x00" + strconv.Itoa(n)`
    and return the first clean value.
  - `ok=false` when all 16 hit.
- `type SealEngine struct` with
  `func NewEngine(s Scanner, t *pytext.Tables, key []byte) *SealEngine`. It implements
  `detect.Engine`:
  - `ScanSegment(text, segmentLocator)` scans, then for each finding builds:
    - the snippet (`_snippet` port: line bounds via `\n` only, as Python's `rfind` and
      `find` use `"\n"`; window plus or minus 40; mask every overlapping finding in
      reverse; cut at 200 code points);
    - `masked_snippet = Redact(snippet)`, or `"[REDACTED]"` if `HasHits` remains, cut at
      200 code points;
    - `location = Redact(segmentLocator)`. If `HasHits` remains, use `"[REDACTED]"`, then
      cut at 200 code points (design V9);
    - `confidence` and the fingerprint over the ORIGINAL-text span.
  - Any fingerprint that is not ok -> return `nil, false`.
  - `HasHits(s)` delegates to the Redactor.

## Key Pitfalls

- All slicing is by code points (`[]rune`), never bytes. The `[len]` suffixes are
  code-point lengths.
- `_snippet` line bounds use only `"\n"`, not `splitlines`. The PEM header check uses
  `splitlines`. Do not mix these up.
- `_snippet` masks the findings of the SAME detection result that overlap the window,
  clipped to the window. Use `max(span.start, start) - start` and `min(span.end, end) -
  start` exactly as Python does.
- `Masker.seal` cuts `safe_snippet[:200]` after the Redactor runs and after the fallback.
  Python cuts in `_snippet` too; port both cuts.
- The word joiner U+2060 in the e-mail mask is intentional. Never "clean" it.
- The fingerprint key is the raw configured bytes. Do not hex- or base64-decode it.

## Tests

- `seal_parity_test.go`:
  - `sealing.json`: every case -> identical `masked_snippet` (decode b64), `location` and
    `external_fingerprint`, in order. The key comes from `key_b64`.
  - `redactor.json`: `HasHits` and `Redact` are identical.
  - `fingerprints.json`: `external`, `item` and `finding` are identical, including the
    `derived_n > 0` cases. Assert at least two derived cases exist.
  - Each loader asserts a non-zero case count. Mismatch messages show indices only.
- `engine_test.go` (input -> expected):
  - A segment with an AWS key sample (assembled from fragments) and an e-mail -> two
    findings; the snippet has no raw value; `HasHits(masked_snippet) == false`.
  - A locator containing a sample secret -> `location == Redact(locator)`, and
    `HasHits(location) == false`.
  - A forced all-hit fingerprint, via a stub `Scanner` that flags every hex string ->
    `ok=false`.

## Verification

```bash
set -o pipefail
cd go
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -count=1 ./internal/detect/... 2>&1 | tee /tmp/telescreen-gdp-p08-gotest.log; echo "exit=$?"
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go vet ./internal/detect/... 2>&1 | tee /tmp/telescreen-gdp-p08-govet.log; echo "exit=$?"
mise exec -- gofmt -l internal/detect   # must print nothing
```

All commands exit 0. The log shows `ok .../internal/detect`, and the gdp-p05 parity tests
still pass.

## Completion Criteria

- [ ] `MaskValue`, `Redactor`, both fingerprint functions, the clean derivation and
      `SealEngine` exist. `var _ Engine = (*SealEngine)(nil)` compiles.
- [ ] The sealing, redactor and fingerprint parity tests pass with zero mismatches.
- [ ] `vet` and `gofmt` are clean, and no gdp-p05 file changed (hashes recorded).
- [ ] Progress Log complete.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)
