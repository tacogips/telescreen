# GDP-P05 Rule Detector Port: Normalization, Validators, 15 Rule Matchers, Merge

**Status**: Ready
**Run 2**: Re-dispatched 2026-10-30 under `impl-plans/active/telescreen-gdp-20261030-h2-dispatch.json` (supersedes h1; same tasks, wave and writePaths). Evidence goes under `tmp/telescreen-gdp-20261030-h2/gdp-p05-detect-core/attempt-<n>/`. Go caches follow the 2026-10-30 Go ruling in `impl-plans/conventions.md` section 7: never an artifact root, never a blocker.
**Design Reference**: design-docs/specs/design-go-gdrive-police.md sections 9.1, 9.2, 9.5
**Created**: 2026-10-30
**Last Updated**: 2026-10-30

## Plan Metadata

- planId: `gdp-p05-detect-core`
- planPath: `impl-plans/active/gdp-p05-detect-core.md`
- wave: 2
- dependsOn: `gdp-p01-foundation`, `gdp-p02-parity-fixtures`
- writePaths:
  - `impl-plans/active/gdp-p05-detect-core.md`
  - `go/internal/pytext/embed.go`
  - `go/internal/pytext/embed_test.go`
  - `go/internal/detect/normalize.go`
  - `go/internal/detect/validators.go`
  - `go/internal/detect/rules.go`
  - `go/internal/detect/rules_credentials.go`
  - `go/internal/detect/rules_pii.go`
  - `go/internal/detect/detector.go`
  - `go/internal/detect/core_test.go`
  - `go/internal/detect/detector_parity_test.go`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` sections 2, 7 and 8. Do not edit `detect/api.go` or
`pytext/tables.go` (gdp-p01), and do not edit `pytext/data` or `testdata/parity`
(gdp-p02). If the Python output looks wrong, it is still the reference; never edit a
fixture to make Go pass.

## Intent and Context

The Go worker must find exactly what the Python `RuleDetector` finds, with the same
`rule_id`, `rule_class`, code-point span and confidence. The masking, sealing and
Redactor on top of it are gdp-p08. The Python reference is:

- `src/telescreen/detection/rule_detector.py:RuleDetector.scan_text`, `_has_keyword`,
  `_inside_identifier`, `_overlap`, `_priority`, `_UUID`;
- `src/telescreen/detection/normalize.py:normalize`, `to_original`;
- `src/telescreen/detection/rules/catalog.py:RULES` (15 rules; catalog order matters);
- `src/telescreen/detection/rules/validators.py`;
- `src/telescreen/core/vocabulary.py:severity_for`, where `high` = credential, token,
  private_key, password, payment_card or national_id.

## Non-goals

- `mask_value`, snippets, Redactor, fingerprints and the `Engine` (gdp-p08).
- `PUBLIC_PREFIXES` (masking, gdp-p08).
- ML and LLM detectors.

## Deliverables (exported API is a contract for gdp-p08 and gdp-p10)

1. `pytext/embed.go`. The `//go:embed data/*.txt` FS is the ONLY package-level variable
   allowed in the module; it is read-only. Add a comment saying so. Export
   `func Default() (*Tables, error)`, which calls `Load` over `fs.Sub(dataFS, "data")`.
2. `normalize.go`: `normalize(text []rune) (norm []rune, indexMap []int)`, the
   per-code-point `unorm.NFKCRune` port of `normalize`, and `toOriginal(span Span,
   indexMap []int) Span`, the exact port of `to_original`.
3. `validators.go`: ports of `luhn_valid`, `known_card_iin`, `my_number_valid`,
   `shannon_entropy`, `jwt_header_valid` and `is_placeholder`.
   - Digits mean `tables.DecimalValue`, never `r - '0'`.
   - Entropy sums over characters in first-occurrence order, using `math.Log2`.
   - The JWT check is non-strict URL-safe base64. Pad to a multiple of 4; a data length
     of 1 mod 4 is invalid. Decode `base64.URLEncoding` (non-strict); trailing bits are
     ignored.
   - The decoded bytes must be valid UTF-8. The JSON must parse under Python
     `json.loads` grammar: the stdlib decoder plus a pre-pass that accepts the literals
     `NaN`, `Infinity` and `-Infinity` outside strings. Leading and trailing whitespace
     is allowed. The top level must be an object with the member key `alg`.
   - `is_placeholder` uses `tables.Strip` and `tables.Lower`.
4. `rules.go`: `type rule struct{ id string; class RuleClass; confidence float64;
   keywords []string; keywordWindow int; validate func([]rune) bool; match matcher }`.
   - `type matcher func(text []rune, start int, t *pytext.Tables) (gStart, gEnd, mEnd int, ok bool)`
     returns the value group span and the whole-match end for a match starting at
     `start`.
   - `findAll` emulates `re.finditer`: scan positions from 0, take the first `start`
     with a match, and continue at `mEnd`. If `mEnd == start` (empty match), advance by
     one.
   - `newRules(t *pytext.Tables) []rule` lists the 15 rules in catalog order.
5. `rules_credentials.go` and `rules_pii.go`: one matcher per rule, split by class
   family:
   - credentials: `aws_access_key_id`, `aws_secret_access_key`, `github_token`,
     `slack_token`, `google_api_key`, `private_key`, `jwt`, `password_assignment`;
   - PII: `credit_card`, `my_number`, `email`, `phone_jp`, `phone_intl`, `address_jp`,
     `address_en`.
6. `detector.go`: `type Detector struct` built by `func NewDetector(t *pytext.Tables) *Detector`.
   `func (d *Detector) ScanText(text string) []RawFinding` implements `detect.Scanner`:
   - Convert to `[]rune` and normalize.
   - Collect UUID spans with the `_UUID` predicate: hex groups 8-4-4-4-12 bounded by
     non-`[0-9A-Za-z]`.
   - For each rule in order and each match: run the validator on the normalized value;
     apply `_inside_identifier`; apply the keyword check (`tables.Lower` of the
     normalized window `[max(0, s-w), min(len, e+w))`, then substring); then map to an
     original span.
   - Stable sort by priority, descending: `(confidence, isHigh, length)`. Use
     `sort.SliceStable` with a strict greater-than comparison.
   - Merge exactly like Python. Removal of overlaps uses value equality (id, class, span
     and confidence equal). The merged finding takes the first maximal-priority entry
     among `[candidate, overlaps...]`.
   - Finally stable sort by start.

## Matcher semantics (the key decisions; a careless port gets these wrong)

- Never use Go's `\b`, `\d`, `\w`, `\s` or `(?i)`. They are ASCII or Unicode-simple-fold
  and differ from Python. Every character test goes through `pytext`:
  - Python `\d` is `DecimalValue(r)` ok.
  - `\w` is `IsWord`.
  - `\s` is `IsSpace`.
  - An `(?i)` letter `x` is `FoldsTo('x', r)`.
  - `\b` at position i is `IsWord(text[i-1]) != IsWord(text[i])`, with out-of-range
    positions counted as non-word.
- Explicit ASCII classes stay ASCII: `[A-Za-z0-9]`, `[A-Z0-9]`, `[0-9a-fA-F]`.
  Under the `address_en` IGNORECASE flag, `[A-Za-z0-9.' -]` becomes
  `FoldsTo(lower(c), r) || isDigit0to9 || r in ".' -"`.
- Python backtracking order must be reproduced:
  - Greedy quantifiers try the longest count first, then shorter.
  - Lazy (`*?`, `{0,24}?`) tries the shortest first.
  - Alternation tries left to right.
  - Trailing assertions (`\b`, `(?!\d)`) are checked after the body, and failure
    backtracks into the body.
  - Implement each matcher as a small explicit backtracking routine over runes, bounded
    by the rule's maximum lengths. Do NOT use one RE2 regexp for a whole rule.
  - RE2 may be used only for a sub-part whose classes are ASCII-only and that has no
    assertion, if that is simpler. A hand loop is preferred.
- `credit_card` `\b((?:\d[ -]?){12,18}\d)\b`: the greedy group repeats from 18 down to
  12, each with an optional separator (greedy: try with the separator first). Then one
  digit, then a trailing `\b`. With Unicode `\d`, Arabic-Indic digits count, and the
  validator uses decimal values.
- `email`: the local part `{1,64}` is greedy and the domain labels are backtracking.
  Check the leading `\b` before the local part and the trailing `\b` after the last
  label.
- `phone_jp`: alternation order is `\d{1,4}-\d{1,4}-\d{4}`, then `\d{9,10}`, then
  `[5789]0-?\d{4}-?\d{4}`, bounded by `(?<!\d)` and `(?!\d)`.
- `password_assignment` `(?i)(?:password|passwd|pwd|secret|pass|パスワード)\s*[:=]\s*([^\s,;]{8,})`:
  - The keyword alternation is in that order, letters compared with `FoldsTo`. `パスワード`
    is literal.
  - `[^\s,;]` uses `IsSpace`.
  - The value is greedy to the end of the run, with no upper bound.
- `private_key`: `[\s\S]*?` is lazy. The END marker search is the first END after
  BEGIN. Optional type words are tried in alternation order.
- `address_jp`: the `[一-龯]` and kana ranges are literal code-point ranges. `[^\n]{0,24}?`
  is lazy. The `〒` branch is greedy `{0,40}`.
- `aws_secret_access_key`: 40 chars of `[A-Za-z0-9/+=]` with `\b` on both sides. Note
  that `/`, `+` and `=` are non-word, so `\b` interacts with them; port it literally.
- The UUID pattern also uses ASCII boundaries `(?<![0-9A-Za-z])`, not `\w`.
- Validators receive the normalized value group; spans are mapped back to original
  offsets afterwards.

## Tests

- `core_test.go` (input -> expected):
  - normalize of `"Ａ１"` -> `"A1"` with map `[0,1,2]`;
  - a ligature expansion maps every output char to the same source index;
  - `toOriginal` across expansions;
  - Luhn and IIN, My Number, entropy, JWT, placeholder unit cases mirroring
    `tests/detection` (read `tests/detection/rules` for cases).
- `detector_parity_test.go`:
  - Load `go/testdata/parity/detector.json` via relative path `../../testdata/parity/`.
  - For every case, `ScanText(decode(text_b64))` equals the fixture findings exactly:
    same order, ids, classes, spans and confidences, compared as `float64 ==`.
  - On mismatch, print the case index and the rule ids, never the text.
  - Assert the fixture has at least 2000 generated cases, so the test cannot pass
    vacuously.
- `embed_test.go`: `pytext.Default()` loads; `UnidataVersion() == unorm.Version()`
  (design 9.2 hard assertion); `PythonVersion()` is non-empty.

## Verification

```bash
set -o pipefail
cd go
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go test -count=1 ./internal/detect/... ./internal/pytext/... 2>&1 | tee /tmp/telescreen-gdp-p05-gotest.log; echo "exit=$?"
GOTOOLCHAIN=local GOFLAGS=-mod=vendor mise exec -- go vet ./internal/detect/... ./internal/pytext/... 2>&1 | tee /tmp/telescreen-gdp-p05-govet.log; echo "exit=$?"
mise exec -- gofmt -l internal/detect internal/pytext   # must print nothing
```

All commands exit 0. The test log shows `ok` for `detect` and `pytext`, and the parity
test reports the case count via `t.Logf`. If `UnidataVersion() != unorm.Version()`,
STOP. That is a design-level blocker (design 9.2): record it in the Progress Log and do
not change the assertion.

## Completion Criteria

- [ ] All 15 rules are ported. The detector parity test passes over the full corpus with
      zero mismatches.
- [ ] The Unicode version assertion passes.
- [ ] No `regexp` usage with `\b`, `\d`, `\w`, `\s` or `(?i)`. Check with
      `grep -nE '\\\\[bdws]|\(\?i\)' go/internal/detect/*.go`, which must print nothing
      for regexp literals.
- [ ] `vet` and `gofmt` are clean, and no file outside `writePaths` changed.
- [ ] Progress Log complete.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)
