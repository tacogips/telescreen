# GDP-P02 Parity Fixtures: Python Generator, pytext Tables, Golden Parity Data

**Status**: Ready
**Design Reference**: design-docs/specs/design-go-gdrive-police.md sections 7, 8.1, 9.1-9.5
**Created**: 2026-10-30
**Last Updated**: 2026-10-30

## Plan Metadata

- planId: `gdp-p02-parity-fixtures`
- planPath: `impl-plans/active/gdp-p02-parity-fixtures.md`
- wave: 1
- dependsOn: none (writes the data format pinned in gdp-p01 section 8; no Go code here)
- writePaths:
  - `impl-plans/active/gdp-p02-parity-fixtures.md`
  - `tools/gen_go_police_fixtures.py`
  - `tools/go_police_fixture_cases.py`
  - `go/internal/pytext/data`
  - `go/testdata/parity`
  - `tests/go_police/test_parity_fixtures_current.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first; all Python rules apply. Do not edit any file under
`src/`: the Python implementation is the reference and must stay unchanged.

## Intent and Context

The Go worker must reproduce Python results exactly (design 9). This plan writes a
deterministic generator. It imports the Python reference code and writes:

- the Python string-semantics tables that Go embeds (`go/internal/pytext/data/*.txt`, in
  the format pinned in gdp-p01 `pytext/tables.go`);
- golden parity fixtures (`go/testdata/parity/*.json`) consumed by the Go tests of
  gdp-p05, gdp-p06 and gdp-p08.

A freshness test makes Python-side drift fail CI.

`pyproject.toml` already sets pytest `pythonpath = ["tools", "."]`, so the test can
`import gen_go_police_fixtures`. Imitate:

- `src/telescreen/collectors/google/sweep.py:_shard` (`telescreen.core.json` builders);
- `tests/collectors/google/support.py` (recording fakes for `DriveApi`/`SheetsApi`/`AdminApi`);
- `tests/support/samples.py` (secret samples assembled from fragments).

## Non-goals

- No Go code. The Go loader exists in gdp-p01, and `embed.go` belongs to gdp-p05.
- No envelope goldens (gdp-p09).
- No change to the Python collector, detector, masking or SDK, not even to add a helper.

## Deliverables

1. `tools/gen_go_police_fixtures.py`: CLI with `--write` (rewrites the outputs) and
   `--check` (exit 1 and print the differing relative paths when any output differs).
   Expose a function `render_outputs() -> tuple[GeneratedFile, ...]`, where
   `GeneratedFile(path: str, content: str)` is a frozen dataclass. The freshness test
   calls it. Keep each file under 1000 lines; put case tables and sample builders in
   `tools/go_police_fixture_cases.py`.
2. Table files in `go/internal/pytext/data/`, as pinned in gdp-p01:
   - The header comes from `sys.version_info` and `unicodedata.unidata_version`.
   - Iterate over every code point from 0 to 0x10FFFF, skipping surrogates
     (0xD800-0xDFFF), and evaluate with Python itself:
     - `word.txt`: `re.fullmatch(r"\w", c)`.
     - `digit.txt`: `c.isdigit()`.
     - `space.txt`: `re.fullmatch(r"\s", c)`. Assert that this equals `c.isspace()` for
       every c, and fail generation if not.
     - `linebreak.txt`: `len(("a" + c + "b").splitlines()) == 2`.
     - `decimal.txt`: `re.fullmatch(r"\d", c)` with `unicodedata.decimal(c)`.
     - `lower.txt`: `c.lower() != c`.
     - `casefold.txt`: `c.casefold() != c`.
     - `ignorecase.txt`: `re.fullmatch("(?i)" + letter, c)`. Also assert that the union
       over all letters equals the code points matched by `re.fullmatch(r"(?i)[A-Za-z]",
       c)`; fail generation if not.
3. Fixtures in `go/testdata/parity/`. Each is one JSON object whose first member is
   `"meta": {"python": ..., "unidata": ..., "generator": "tools/gen_go_police_fixtures.py"}`.
   All text that could contain sample secrets or content is base64 of UTF-8 in a `*_b64`
   member. The fingerprint key is the fixed label
   `go-parity-fixture-key-0000000000000000` (at least 32 bytes), stored as `key_b64`.
   - `exposure.json`: `cases[]: {name, permissions[]: {type, role, domain|null,
     email_address|null, allow_file_discovery}, drive_id|null, internal_domains[],
     expected: {visibility, external_principal_count, shared_drive}}`.
     - Computed by `telescreen.collectors.google.exposure.drive_exposure`.
     - Include: anyone with and without discovery; owner-only; external user; external
       group; internal user; domain internal and external; no internal domains with
       user grants (`unknown`); mixed-case domains; non-ASCII domains whose `casefold`
       differs from `lower` (for example `ß`); email without `@`; shared drive.
     - Emails use `example.com`/`example.org` style domains only.
   - `locators.json`:
     - `refs[]: {kind, file_id, locator}`, from `GoogleAdapter._ref(...).raw_locator`.
     - `shards[]: {corpus_key, file_ids[], shard_key, descriptor, weight}`, from
       `sweep._shard`. Include unsorted input ids.
     - `queries[]: {kind, selector:{drive_ids, folder_ids, mime_types}, corpus_key,
       cursor|null, expected:{q, corpora, drive_id|null, page_token|null, page_size,
       subject|null}}`. Capture these by running `GoogleSweep.partition` against a
       recording fake Drive and fake Admin. Include user, drive and folder corpora;
       quotes and backslashes in ids; each kind; and the gdrive kind with and without
       `mime_types`.
     - `root[]`: the root-page corpora for non-delegated selectors, from `_root_page`
       with `admin=None`.
   - `matching.json`: `cases[]: {kind, selector, corpus_key, file:{id, mime_type,
     trashed, drive_id|null, parents[]}, matches, included}`, from `_matches`, `_included`
     and `_text_like` over a mime grid (text/*, json, csv, yaml, xml, `+xml`, `+json`,
     image/*, pdf, Google-native docs and sheets).
   - `segments.json`:
     - `lines[]: {text_b64, segments[]: {locator, text_b64}}`, from `_line_segments`.
       Cover every `splitlines` break character, `\r\n`, a trailing break, an empty
       string and a BOM.
     - `sheets[]: {titles[], ranges[], value_ranges[]: {range, values: [[<raw JSON
       cell>]]}, segments[], item_count}`. `ranges` are captured from
       `GoogleAdapter(kind=GSHEETS)._fetch_content` with recording fakes. Include titles
       containing `'`, `!` and spaces; cells that are int, float (`1.0`, `1e16`, `1e-5`,
       `0.0001`, `123456789.125`), bool, empty string and ragged rows.
   - `fingerprints.json`:
     - `external[]: {value_b64, fp}` (SDK `masking.external_fingerprint`).
     - `item[]: {item_key_b64, fp}` (SDK `masking.item_fingerprint`).
     - `finding[]: {rule_id, rule_class, raw_b64, normalized_b64, fp, derived_n}`.
     - The `finding` reference (design 9.4):
       `message = "v1\0" + rule_id + "\0" + Fingerprinter.normalize_for_fingerprint(raw,
       RuleClass(rule_class))` and `fp0 = external_fingerprint(message, key)`. If
       `Redactor.default().has_hits(fp0)`, use the first `n` in 1..16 with
       `external_fingerprint(message + "\0" + str(n), key)` clean; `derived_n` is 0 when
       `fp0` is clean.
     - Search deterministically for at least two raw values that need `derived_n > 0`
       and include them. Include card, phone, national_id and email values with spaces,
       hyphens, fullwidth digits, uppercase e-mail and a combining accent.
   - `detector.json`: `cases[]: {text_b64, findings[]: {rule_id, rule_class, start, end,
     confidence}}`, from `telescreen.detection.RuleDetector().scan_text`.
     - Handpicked boundary cases for every rule in
       `telescreen.detection.rules.catalog.RULES`: Unicode letters and digits adjacent to
       tokens; fullwidth input; Arabic-Indic digits; UUID partial overlap; overlapping
       same-class merge; different-class overlap; the keyword window edge for the AWS
       secret; entropy near 4.0; placeholder passwords; JWT headers with bad padding,
       NaN or a non-object; Dotted and dotless i and the Kelvin sign with
       `password_assignment` and `address_en`; lazy `address_jp`; multi-line text.
     - Plus at least 2000 cases from `random.Random(20261030)`, built by concatenating
       fragments (rule-shaped tokens, digits, separators, Unicode neighbours,
       keywords, UUIDs, whitespace kinds).
     - Record the count in `meta.generated`.
   - `sealing.json`: `cases[]: {text_b64, locator, findings[]: {rule_id, rule_class,
     masked_snippet_b64, location, confidence, external_fingerprint}}`.
     - `masked_snippet` and `location` come from
       `telescreen.masking.seal.Masker(Fingerprinter(key, "fixture"),
       Redactor.default()).seal(ScanItem(...), DetectionResult(...))`.
     - `external_fingerprint` uses the derivation above with the original-text span.
     - Include long lines (window cut at plus or minus 40 and at 200), multi-finding
       lines, e-mail masks, PEM headers, phone masks, card masks and a snippet that keeps
       hits after redaction (falls back to `[REDACTED]`).
   - `redactor.json`: `cases[]: {text_b64, has_hits, redacted_b64}` from
     `Redactor.default()`. Include hex HMAC strings with and without hits, Drive-style
     file ids, `user:` corpus keys with 21 digits, and plain masked outputs.
4. `tests/go_police/test_parity_fixtures_current.py`. Tests:
   - each `render_outputs()` entry equals the file on disk byte for byte, and on mismatch
     the assertion message names the paths only;
   - `detector.json` `meta.generated >= 2000`;
   - `fingerprints.json` has at least two `derived_n > 0` cases;
   - every table file starts with the same header as the running Python.

## Key Pitfalls

- Determinism: no wall clock and no `hash()`-ordered sets in output. Sort ranges and
  keys. Use a fixed `random.Random` seed. Serialize with `telescreen.core.json.dump_json`
  over `json_object`/`json_array` builders, and end files with a newline. Never bind a
  `dict` (typelint TL005).
- gitleaks: no contiguous secret-shaped literal in `tools/*.py` (build samples from
  fragments). No raw sample in fixture JSON (base64 only). Run the gitleaks pre-commit
  hook on your files; see Verification.
- Do not monkeypatch production modules. The recording fakes implement the same methods
  as `DriveApi`, `SheetsApi` and `AdminApi` and are passed through constructors or
  factories.
- The full code-point sweep is slow. Generate each table in one pass and keep runtime
  under about 60 s. The freshness test calls the same functions.
- Do not ship a fixture that the Python reference itself cannot reproduce. The `--check`
  run right after `--write` must exit 0.

## Tests (input -> expected)

- `--write` then `--check` -> exit 0, with no diff.
- Changing one byte of `go/testdata/parity/exposure.json` makes `--check` exit 1 and name
  the file. Restore the file afterwards, and record this mutation check in the Progress
  Log only.
- The freshness test file passes.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen python tools/gen_go_police_fixtures.py --write 2>&1 | tee /tmp/telescreen-gdp-p02-gen.log; echo "exit=$?"
uv run --frozen python tools/gen_go_police_fixtures.py --check 2>&1 | tee /tmp/telescreen-gdp-p02-check.log; echo "exit=$?"
uv run --frozen ruff format --check --no-cache tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police
uv run --frozen ruff check --no-cache tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police
uv run --frozen ty check tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police/test_parity_fixtures_current.py
uv run --frozen pytest -p no:cacheprovider tests/go_police/test_parity_fixtures_current.py 2>&1 | tee /tmp/telescreen-gdp-p02-pytest.log; echo "exit=$?"
uv run --frozen python tools/typelint.py > /tmp/telescreen-gdp-p02-typelint.log 2>&1; grep -E '^(tools/gen_go_police_fixtures|tools/go_police_fixture_cases|tests/go_police)' /tmp/telescreen-gdp-p02-typelint.log
mise exec -- gitleaks dir --redact --no-banner tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py go/testdata/parity go/internal/pytext/data tests/go_police 2>&1 | tee /tmp/telescreen-gdp-p02-gitleaks.log; echo "exit=$?"
```

All commands exit 0; the typelint grep prints nothing; the pytest log shows the
freshness tests passed with zero skipped. If `gitleaks dir` does not accept several paths
with this version, run it once per path and record each run.

## Completion Criteria

- [ ] All 8 table files and 8 fixture files exist with headers or `meta`, and `--check`
      exits 0.
- [ ] Detector corpus has at least 2000 generated cases plus handpicked cases for all 15
      catalog rules; at least two derived fingerprint cases exist.
- [ ] Freshness test passes; ruff, ty and typelint are clean for owned paths; gitleaks is
      clean.
- [ ] Progress Log has hashes, commands, exit codes and log paths.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)
