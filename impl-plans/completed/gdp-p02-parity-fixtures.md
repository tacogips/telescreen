# GDP-P02 Parity Fixtures: Python Generator, pytext Tables, Golden Parity Data

**Status**: Completed (accepted in run 2 at `fb93c5e`, carried as an accepted dependency through h5 at `cae1af3`; archived to `impl-plans/completed/` by the run-5 serial finalization, 2026-10-31)
**Run 2**: Re-dispatched 2026-10-30 under `impl-plans/active/telescreen-gdp-20261030-h2-dispatch.json` (supersedes h1; same tasks, wave and writePaths). Evidence goes under `tmp/telescreen-gdp-20261030-h2/gdp-p02-parity-fixtures/attempt-<n>/`. Continue from the run-1 partial session (keep and reconcile `tools/gen_go_police_fixtures.py` and the eight `go/internal/pytext/data` tables; do not duplicate them) per the Owner resolution in the Progress Log, and re-run every final gate.
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
mise exec -- gitleaks dir --config .gitleaks.toml --redact --no-banner tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py go/testdata/parity go/internal/pytext/data tests/go_police 2>&1 | tee /tmp/telescreen-gdp-p02-gitleaks.log; echo "exit=$?"
```

All commands exit 0; the typelint grep prints nothing; the pytest log shows the
freshness tests passed with zero skipped. If `gitleaks dir` does not accept several paths
with this version, run it once per path and record each run.

## Completion Criteria

- [x] All 8 table files and 8 fixture files exist with headers or `meta`, and `--check`
      exits 0.
- [x] Detector corpus has at least 2000 generated cases plus handpicked cases for all 15
      catalog rules; at least two derived fingerprint cases exist.
- [x] Freshness test passes; ruff, ty and typelint are clean for owned paths; gitleaks is
      clean.
- [x] Progress Log has hashes, commands, exit codes and log paths.

## Progress Log

(append sessions per `impl-plans/conventions.md` section 8)

### Session: 2026-10-30
**Tasks Completed**: Implemented Unicode table rendering and generated eight table files.
**Tasks In Progress**: Generator CLI and all eight parity fixtures, case builders, freshness
 test, mutation check, and required final verification remain outstanding.
**Blockers**: None identified.
**Reconciliation requests**: None.
**Hashes**: `tools/gen_go_police_fixtures.py` before=absent after=14d55692aa40614c5014776c0000634208e1bd1a2565ba2d726e1b51f8e5e04d.
Table files before=absent; after SHA-256: casefold=0d4787463413685cae064def829ef16b34e6c2de1aed7e77a940de0b3305366c, decimal=9ede9ee34855cffc330a4ccb45107e8b992d5b86ea9b0604bdedcac1e7150a81, digit=ea61b4f89b20f3976ba413b87d5fa77f02bee209c1a5c42265ed390a19bf50b7, ignorecase=e516e40a802fc5a876ad2acfc183231a7834428c252c9765b838f533acbfa6b2, linebreak=54e5ecc81e97beb6fda560e843a986b79ed0ecff4893d8f248bb13ae47532bd1, lower=245aec7f7796ffc98922c6d9562586d280402d9b4121910d56a6c34afbc9ff08, space=f6f650ca9a50a889feaf6f47539644c84fc0fb7850e5a4fb051b63e4b3b2cea9, word=c2185d10e53198fa3070b80cc461ff71bea77d8d3fda984e9a221fad2e8ded3f.
**Verification**: Scoped ruff and ty checks exit 0; owned-path typelint filter exit 0;
regeneration comparison exit 0 for eight tables. Logs under
`tmp/telescreen-gdp-20261030-h1/gdp-p02-parity-fixtures/` named `ruff.log`, `ty.log`,
`typelint.log`, and `table-freshness.log` respectively. These are partial checks, not the
plan's final gates. Initial ruff failed on line length, corrected by scoped formatting
(`ruff-initial.log`).
**Notes**: Python 3.12.13, Unicode 15.0.0. Formal reviews and integration are downstream.

### Owner resolution (2026-10-30): continue from the partial session

Run 1 ended before this plan finished; the wave was blocked by gdp-p01 (Go cache policy,
now resolved by the 2026-10-30 Go ruling in `impl-plans/conventions.md` section 7). The
generator's Unicode table rendering and the eight `go/internal/pytext/data/*.txt` tables
committed in the continuation checkpoint are kept. Continue with the outstanding work
(generator CLI, all parity fixtures, case builders, freshness test, mutation check and the
plan's final verification). Re-run every final gate yourself and leave `priorVerification`
empty; the partial checks of run 1 are superseded.

### Session: 2026-10-30 (run 2, partial)
**Tasks Completed**: Added a seeded 2,000-input detector case builder using synthetic
samples and Unicode, keyword, separator and whitespace fragments.
**Tasks In Progress**: The generator CLI, eight parity fixture families, handpicked rule
coverage, derived fingerprint search, freshness test, mutation check and full final
verification remain outstanding. The case builder is not yet connected to a fixture.
**Blockers**: None identified.
**Reconciliation requests**: None.
**Hashes**: `tools/go_police_fixture_cases.py` before=absent
 after=a5cdc9f086f2c5b3d9a9a504f40f79865c2f465fc0a492d0e2078bd6f5305bfb;
 `impl-plans/active/gdp-p02-parity-fixtures.md` before=66d67a5d326fd236e6e52256250d8f130d944c567684ec5c25b52fe8ddb08b0d (after hash recorded in session output).
**Verification**: All logs below are in `tmp/telescreen-gdp-20261030-h2/gdp-p02-parity-fixtures/attempt-1/`.
`uv run --frozen ruff format --check --no-cache tools/go_police_fixture_cases.py` exit 0 (`format-final.log`);
`uv run --frozen ruff check --no-cache tools/go_police_fixture_cases.py` exit 0 (`ruff-final.log`);
`uv run --frozen ty check tools/go_police_fixture_cases.py` exit 0 (`ty-final.log`);
`uv run --frozen python -c 'from tools.go_police_fixture_cases import generated_detector_cases; a = generated_detector_cases(); assert len(a) == 2000 and a == generated_detector_cases(); print("2 assertions passed")'` exit 0 (`cases-final.log`);
`uv run --frozen python tools/typelint.py > /tmp/telescreen-gdp-p02-owned-typelint-full.log 2>&1; ! grep -E '^tools/go_police_fixture_cases' /tmp/telescreen-gdp-p02-owned-typelint-full.log` exit 0 (`typelint-final.log`);
`mise exec -- gitleaks dir --redact --no-banner tools/go_police_fixture_cases.py` exit 0 (`gitleaks-final.log`).
**Notes**: These scoped checks do not satisfy the plan's final gates. The initial format
check failed and the file was formatted before the final checks (`format.log`). Formal
reviews and integration remain downstream.

### Session: 2026-10-30 (run 2, continuation 1)
**Tasks Completed**: Added Python-reference builders for exposure, MIME matching, line
segments, detector findings, redaction, masking and fingerprints, including deterministic
search for two redactor-triggered base fingerprints. Added four focused builder tests and
corrected existing generator unpack declarations for typelint.
**Tasks In Progress**: The CLI, full fixture outputs, locator and sheet fixtures, complete
handpicked detector/sealing coverage, freshness and mutation checks, and final output gates
remain unfinished. The current tests cover builders, not on-disk freshness.
**Blockers**: None identified.
**Reconciliation requests**: None.
**Hashes**: `tools/gen_go_police_fixtures.py` before=14d55692aa40614c5014776c0000634208e1bd1a2565ba2d726e1b51f8e5e04d after=53f5f0526389311b501c68c35f8d8e40b62dd91de57cbd85eb03c06a3e4cf01d;
`tools/go_police_fixture_cases.py` before=a5cdc9f086f2c5b3d9a9a504f40f79865c2f465fc0a492d0e2078bd6f5305bfb after=8de9d17a4f7ca8709aae317f49cb6898ee577dd5d4a6b6ff40e0e3c6804f61f5;
`tests/go_police/test_parity_fixtures_current.py` before=absent after=6cd0368a1a957162640db71d998285fe23ec9c3962c08aca3cb5f3c2bc49d77a;
`impl-plans/active/gdp-p02-parity-fixtures.md` before=a61e0972dcaac6b4e2ffee0ea6f9397f2cbf6160fc9a373e7963196224f035e7 (after hash recorded in session output).
**Verification**: Logs are under `tmp/telescreen-gdp-20261030-h2/gdp-p02-parity-fixtures/attempt-2/`.
`uv run --frozen pytest -p no:cacheprovider tests/go_police/test_parity_fixtures_current.py` exit 0, four passed, zero skipped (`test-final.log`);
`uv run --frozen ruff format --check --no-cache tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police` exit 0 (`format-final.log`);
`uv run --frozen ruff check --no-cache tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police` exit 0 (`ruff-final.log`);
`uv run --frozen ty check tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police/test_parity_fixtures_current.py` exit 0 (`ty-final.log`);
`uv run --frozen python tools/typelint.py > /tmp/gdp-p02-attempt2-final-typelint.log 2>&1; ! grep -E '/(tools/gen_go_police_fixtures.py|tools/go_police_fixture_cases.py|tests/go_police/)' /tmp/gdp-p02-attempt2-final-typelint.log` exit 0 (`types-final.log`);
`mise exec -- gitleaks dir --redact --no-banner tools/go_police_fixture_cases.py` exit 0 (`leaks-helper-final.log`);
`mise exec -- gitleaks dir --redact --no-banner tests/go_police` exit 0 (`leaks-test-final.log`).
**Notes**: An initial scoped ruff check failed on import order (`ruff.log`); corrected and
rerun. This is partial implementation and the above tests are not freshness evidence.
Formal reviews and integration remain downstream.

### Session: 2026-10-30 (run 2, continuation 2)
**Tasks Completed**: Added generator CLI and eight parity outputs, offline locator and
Sheets capture, all-rule handpicked cases and an on-disk freshness test. Regeneration
and check pass; the deliberate one-byte mutation was detected and restored.
**Tasks In Progress**: The parity gitleaks scan fails; the requested sealing fallback and
several detailed boundary cases remain to be confirmed. Do not treat this plan as complete.
**Blockers**: The scan reports 292 matches in generated parity fixtures; inspect synthetic
fixture representation and resolve without weakening secret detection.
**Reconciliation requests**: None.
**Hashes**: `tools/gen_go_police_fixtures.py` before=53f5f0526389311b501c68c35f8d8e40b62dd91de57cbd85eb03c06a3e4cf01d after=e5946b0c1b8ba58e35b53b210d2cbde4a1183aaddb1030bbda39b403455c0529;
`tools/go_police_fixture_cases.py` before=8de9d17a4f7ca8709aae317f49cb6898ee577dd5d4a6b6ff40e0e3c6804f61f5 after=eea300103de1c8f43e3f366a23f5a81de85636b7deee0011a14d8136ae67b15f;
`tests/go_police/test_parity_fixtures_current.py` before=6cd0368a1a957162640db71d998285fe23ec9c3962c08aca3cb5f3c2bc49d77a after=b157aa442961b1de8b683620c59987d6557540bae95704d8c9442c8c679c9d4e;
`impl-plans/active/gdp-p02-parity-fixtures.md` before=e0f1492af05d8f6c94ce5e5f138185d6ec4eb5ee5fbcfd3088b8f7a35fd3e254 (after hash recorded in session output).
Generated file hashes are recorded in `attempt-3/output-final-hashes.log`; previous table
hashes and absent fixture state are in `attempt-3/output-before-hashes.log`.
**Verification**: Logs below are under `tmp/telescreen-gdp-20261030-h2/gdp-p02-parity-fixtures/attempt-3/`.
`PYTHONDONTWRITEBYTECODE=1 uv run --frozen python tools/gen_go_police_fixtures.py --write` exit 0 (`write-final.log`);
`PYTHONDONTWRITEBYTECODE=1 uv run --frozen python tools/gen_go_police_fixtures.py --check` exit 0 (`check-final.log`);
`PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -p no:cacheprovider tests/go_police/test_parity_fixtures_current.py` exit 0, five passed, zero skipped (`pytest-final.log`);
`uv run --frozen ruff format --check --no-cache tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police` exit 0 (`format-final.log`);
`uv run --frozen ruff check --no-cache tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police` exit 0 (`ruff-final.log`);
`uv run --frozen ty check tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police/test_parity_fixtures_current.py` exit 0 (`ty-final.log`);
`PYTHONDONTWRITEBYTECODE=1 uv run --frozen python tools/typelint.py > /tmp/gdp-p02-3-typelint-final2.log 2>&1; ! grep -E '/(tools/gen_go_police_fixtures.py|tools/go_police_fixture_cases.py|tests/go_police/)' /tmp/gdp-p02-3-typelint-final2.log` exit 0 (`typelint-final.log`);
`mise exec -- gitleaks dir --redact --no-banner go/testdata/parity` exit 1, 292 matches (`gitleaks-parity.log`, redacted detail `gitleaks-report.json`);
separate scans of generator, case helper, table directory and test directory exit 0 (`gitleaks-generator.log`, `gitleaks-cases.log`, `gitleaks-tables.log`, `gitleaks-tests.log`).
Mutation control exit 1 as expected and identified only the mutated path (`mutation.log`);
original bytes restored. Initial generation failures and initial lint failure are retained in
`write.log`, `write-retry.log`, and `ruff-initial.log`.
**Notes**: No `src/` files were edited. Formal reviews and integration remain downstream.

### Owner resolution (2026-10-30, run 2): parity fixtures must pass gitleaks

The repository gitleaks scan decodes base64 (and hex and percent-encoding) before matching,
so base64 alone does not keep synthetic secret samples out of its view: run 2 found 292
matches in `go/testdata/parity`. Owner decision: keep every `*_b64` field name, but each
`*_b64` value is now standard padded base64 of the UTF-8 bytes XOR-ed byte by byte with the
repeating ASCII key `telescreen-go-parity-fixture` (obfuscation, not encryption). This
applies uniformly to every `*_b64` field in every parity fixture, `key_b64` included. Each
fixture file carries a top-level `"obfuscation": "xor-v1:telescreen-go-parity-fixture"`
member, and loaders must reject any other value. Do NOT add a gitleaks allowlist for fixture
paths and do not weaken any gitleaks rule; the gitleaks scan of `go/testdata/parity` must
exit 0. Go and Python tests decode with a small local helper (base64 decode, then XOR with
the same key).

Regenerate every fixture with this encoding, update the freshness test and the case
helpers, re-run the plan's full final verification including the gitleaks scan of
`go/testdata/parity`, and leave `priorVerification` empty. Then finish the remaining
boundary cases and the sealing fallback named in the run-2 session.

### Session: 2026-10-30 (run 2, owner encoding repair)
**Tasks Completed**: Applied XOR-v1 to every encoded field, added the fixture marker and
strict Python decode test, added sealing fallback and detector boundary examples, and
regenerated the fixtures. The freshness test reports six passed and zero skipped.
**Tasks In Progress**: The required parity secret scan still fails with four
generic-api-key findings on required `shard_key`, `item_key_b64` and `key_b64` fields.
The fixed key and computed shard keys are required by this plan; a resolution preserving
the schema and scanner rules needs owner direction. Other detailed boundary coverage has
not been independently established as complete.
**Blockers**: The parity scan exits 1; no allowlist or scanner rule was changed.
**Reconciliation requests**: Clarify an acceptable fixture representation for the four
remaining scanner findings while retaining the specified field names and exact values.
**Hashes**: Before hashes and final generated/source hashes are in
`tmp/telescreen-gdp-20261030-h2/gdp-p02-parity-fixtures/attempt-4/before-hashes.log`
and `attempt-4/hashes.log` respectively.
**Verification**: Logs in `tmp/telescreen-gdp-20261030-h2/gdp-p02-parity-fixtures/attempt-4/`:
generator `--write` and `--check` exit 0 (`write.log`, `check.log`); scoped ruff format,
ruff check, ty and typelint exit 0 (`format.log`, `ruff.log`, `ty.log`, `typelint.log`);
freshness test exits 0 with six passed and zero skipped (`test.log`). Separate gitleaks
scans of generator, helper, tables and tests exit 0 (`leaks-generator.log`,
`leaks-cases.log`, `leaks-tables.log`, `leaks-tests.log`); parity scan exits 1 with four
findings (`leaks-parity.log`, redacted `leaks-report.json`). No `src/` file was changed.
Formal reviews and integration remain downstream.

### Owner resolution (2026-10-30, run 2, wave 2): remaining gitleaks false positives

The four remaining `generic-api-key` matches (two `shard_key` SHA-256 digests in
`locators.json`, `item_key_b64` and `key_b64` in `fingerprints.json`) are not secrets. The
owner added a narrow `[[allowlists]]` entry to `.gitleaks.toml` (`condition = "AND"`: those
two files AND those exact field patterns only). This refines the earlier "no allowlist"
wording: path-wide allowlists stay forbidden; field-exact allowlists for provably
non-secret values follow the existing repository practice. `gitleaks dir <subdir>` does not
load the repository config, so every gitleaks gate in this plan runs from the repository
root with `--config .gitleaks.toml` (the pre-commit hook loads it the same way). With that,
`mise exec -- gitleaks dir --config .gitleaks.toml --redact --no-banner go/testdata/parity`
exits 0 on the current fixtures. Keep the fixture field names and values unchanged. Re-run
all final gates and leave `priorVerification` empty.

### Session: 2026-10-31 (run 2, owner scan resolution)
**Tasks Completed**: Re-generated and checked all fixtures without changing their hashes;
ran the full scoped gates using the owner-authorized field-specific scanner configuration.
The prior scanner blocker is resolved. Formal reviews and integration remain downstream.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: Source and generated before and after hashes match in
`tmp/telescreen-gdp-20261030-h2/gdp-p02-parity-fixtures/attempt-5/before-hashes.log`
and `attempt-5/after-generation-hashes.log`; the plan before hash is
`1278cb8b4194cb3db76a4a90a2d9d2bd1c85a54f0d476864f6b0c9b27e2ed8ce`.
The plan after hash is recorded in `attempt-5/final-hashes.log`.
**Verification**: All logs below are in `tmp/telescreen-gdp-20261030-h2/gdp-p02-parity-fixtures/attempt-5/`.
`PYTHONDONTWRITEBYTECODE=1 uv run --frozen python tools/gen_go_police_fixtures.py --write`
and `--check` exit 0 (`write.log`, `check.log`).
`uv run --frozen ruff format --check --no-cache tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police`
and `uv run --frozen ruff check --no-cache tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police`
exit 0 (`format.log`, `ruff.log`).
`uv run --frozen ty check tools/gen_go_police_fixtures.py tools/go_police_fixture_cases.py tests/go_police/test_parity_fixtures_current.py`
exits 0 (`ty.log`). `PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -p no:cacheprovider tests/go_police/test_parity_fixtures_current.py`
exits 0 with six passed and zero skipped (`test.log`).
`uv run --frozen python tools/typelint.py` exits 0; the owned-path filter is empty
(`typelint-full.log`, `typelint.log`).
`mise exec -- gitleaks dir --config .gitleaks.toml --redact --no-banner <path>` exits 0
separately for the generator, case helper, parity directory, tables and tests
(`leaks-tools_gen_go_police_fixtures.py.log`, `leaks-tools_go_police_fixture_cases.py.log`,
`leaks-go_testdata_parity.log`, `leaks-go_internal_pytext_data.log`, `leaks-tests_go_police.log`).
No `src/` file was changed by this plan.
