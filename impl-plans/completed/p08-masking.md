# P08 Masking, Redactor, IndexGuard and Log Masking Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-masking.md (Components, Masking policy, Fingerprints, Masked snippets, Logging and tracebacks, IndexGuard)
**Created**: 2026-10-08
**Last Updated**: 2026-10-09

## Plan Metadata

- planId: `p08-masking`
- planPath: `impl-plans/active/p08-masking.md`
- wave: 3
- dependsOn: `p03-detection-rules`, `p04-ports`
- writePaths:
  - `impl-plans/active/p08-masking.md`
  - `src/telescreen/masking`
  - `tests/masking`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Masking is the only component allowed to read spans of raw text. It turns raw detector
spans into persistable `MaskedFinding`s (masked value, masked snippet, HMAC fingerprint)
and provides three rule-based guards used everywhere:

- the `Redactor`, for free text;
- the `IndexGuard`, for search documents;
- the masking log formatter, for logs and tracebacks.

The DLP gate itself is in notifications (p17) and is built on the `Redactor` and the
detectors.

## Non-goals

- No DLP gate, no persistence, no API error handlers (p10 installs them using
  `safe_errors`).

## Deliverables (`src/telescreen/masking/`)

| Module | Contract |
|--------|----------|
| `__init__.py` | docstring |
| `mask.py` | `mask_value(value: str, rule_id: str, rule_class: RuleClass) -> str` following the policy table in design-masking.md |
| `fingerprint.py` | `Fingerprinter(key: bytes = field(repr=False), key_id: str)` dataclass; `@staticmethod from_secret(secret: SecretValue) -> Fingerprinter` (raises `ConfigError` when the key is shorter than 32 bytes); `fingerprint(rule_id: str, raw: str, rule_class: RuleClass) -> str`; `resource_key(source: str, raw_locator: str) -> str`; `external(source: str, external_fingerprint: str) -> str`; `normalize_for_fingerprint(raw: str, rule_class: RuleClass) -> str` |
| `redact.py` | `RedactionResult(text: str, hit_count: int, rule_classes: tuple[RuleClass, ...])`; `Redactor(detector: RuleDetector)` dataclass: `redact(text: str) -> RedactionResult`, `has_hits(text: str) -> bool`; `@staticmethod default() -> Redactor` |
| `seal.py` | `Masker(fingerprinter: Fingerprinter, redactor: Redactor)`: `seal(item: ScanItem, result: DetectionResult) -> list[MaskedFinding]`; `display_locator(raw_locator: str) -> str` (= `redactor.redact(raw_locator).text`) |
| `index_guard.py` | `GuardVerdict(accepted: bool, field_names: tuple[str, ...], rule_ids: tuple[str, ...])`; `IndexGuard(detector: RuleDetector)`: `check(fields: Sequence[tuple[str, str]]) -> GuardVerdict` (field name and value pairs; any hit in any field rejects the whole document) |
| `log_masking.py` | `@dataclass(eq=False) class MaskingFormatter(logging.Formatter)` with the field `redactor: Redactor` (call `super().__init__(fmt=...)` in `__post_init__`); `configure_logging(level: str, redactor: Redactor | None = None) -> None`; `install_excepthook(redactor: Redactor) -> None` |
| `safe_errors.py` | `SafeError(code: ErrorCode, error_id: str)`; `safe_error_for(exc: BaseException) -> SafeError` (`TelescreenError` keeps its code; anything else -> `internal_error`); `new_error_id() -> str` |

Behavior details:

- Masking policy, exactly as in the design table:
  - known-prefix credentials and tokens: prefix plus `*` repeated to the remaining length
    (capped at 16) plus `[<len>]`, for example `ghp_****************[40]`;
  - private keys: header line plus ` [REDACTED <n> chars]`;
  - jwt, password and generic secrets: `********[<len>]`;
  - payment cards: `**** **** **** <last4>`;
  - national_id: twelve `*`;
  - email: first local character, then `***@`, then the domain. An invisible U+2060 word
    joiner between the stars keeps the visible mask from matching the email rule again;
  - phone: digits masked except the last 2, keeping separators;
  - address: `[ADDRESS REDACTED]`.
  The prefix list comes from `telescreen.detection.rules.catalog.PUBLIC_PREFIXES`.
- Invariant (property test): the masked output keeps at most 4 characters of the secret
  part, and at most 25% of its length. The secret part excludes the public prefix, the PEM
  header and the email domain. For example, a 4-digit card tail of a 16-digit card is
  25%.
- Fingerprint: `hex(HMAC-SHA256(key, "v1\0" + rule_id + "\0" + normalized))`.
  - Normalization: NFKC; for payment_card, national_id and phone, strip spaces and
    hyphens; lowercase emails.
  - `key_id` is the first 8 hex characters of `sha256(key)`.
  - `resource_key = hex(HMAC(key, "resource\0" + source + "\0" + raw_locator))`.
  - `external = hex(HMAC(key, "ext\0" + source + "\0" + external_fingerprint))`.
- Snippet (`seal`):
  1. Take a window of 40 characters on each side of the span, clamped to the line
     boundaries.
  2. Replace EVERY detected span inside the window with its masked value (all findings
     of the result, not only the current one).
  3. Run `Redactor` on the result; any remaining hit makes the snippet `[REDACTED]`.
  4. Truncate to 200 characters.
  - `location` is `item.locator` passed through `Redactor`.
  - `severity` comes from `severity_for`.
- `Redactor.redact` masks every `RuleDetector.scan_text` span using `mask_value`. It is
  idempotent: redacting already-redacted text gives zero hits. Masked forms must not match
  rules by construction, so make sure `ghp_****` and `**** **** **** 1234` do not
  re-trigger.
- `MaskingFormatter.format(record)` calls `super().format(record)` (this includes the
  exception and stack text) and then `redactor.redact(...)` on the full string.
  `configure_logging`:
  - installs one `StreamHandler` on the root logger with this formatter;
  - sets the level;
  - makes `uvicorn`, `uvicorn.error`, `uvicorn.access`, `httpx` and `sqlalchemy`
    propagate to root with no handlers of their own;
  - sets `httpx`, `httpcore` and `anthropic` to WARNING;
  - is idempotent (calling it twice does not duplicate handlers).
- `install_excepthook` replaces `sys.excepthook` with a function that logs through the
  root logger (and thus the formatter).

## Key Pitfalls

- Never log, raise or `repr` raw values inside this package. `ScanItem.text` is
  `repr=False` already; do not build f-strings with spans of it except to produce masked
  output.
- Overlapping spans in `seal`: mask from right to left so offsets stay valid.
- The `Redactor` must be cheap to construct once and reuse. Build the rule detector once
  (`Redactor.default()`).

## Tests (`tests/masking/`)

- For every sample in `tests.support.samples.all_samples()`:
  - `mask_value` output satisfies the invariant (count kept characters against the
    secret part);
  - `find_leaks(masked) == ()`;
  - `Redactor.redact(masked).hit_count == 0`.
- Fingerprints: same value with different separators (card with and without spaces,
  full-width digits) -> the same fingerprint; a different key -> a different fingerprint;
  `key_id` has 8 hex characters; a key shorter than 32 bytes -> ConfigError.
- `seal` on a line containing two secrets -> two findings; each snippet contains neither
  raw sample (use `assert_no_leaks`); the snippet is at most 200 characters.
- `IndexGuard.check([("title", "ok"), ("body", sample)])` -> rejected with
  `field_names == ("body",)` and a rule id; masked text -> accepted.
- Logging: configure, then log `"value=%s"` with a sample argument, and log
  `exc_info` from an exception whose message contains a sample. The captured stream
  (handler pointed at `io.StringIO`) has no leaks. `configure_logging` twice -> one
  handler.
- `safe_error_for(ValueError(sample))` -> code `internal_error`, and
  `repr(SafeError)` has no sample.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/masking tests/masking
uv run --frozen ruff check --no-cache src/telescreen/masking tests/masking
uv run --frozen ty check src/telescreen/masking tests/masking
uv run --frozen pytest -p no:cacheprovider tests/masking 2>&1 | tee /tmp/telescreen-p08-masking-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p08-masking-typelint.log 2>&1; ! grep -E '(^|/)(src/telescreen/masking/|tests/masking/)' /tmp/telescreen-p08-masking-typelint.log
```

## Completion Criteria

- [x] All modules and symbols above exist with the exact names
- [x] The masking invariant property test covers every sample
- [x] Log capture and traceback tests show no leaks
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 22:23 JST
**Tasks Completed**: Implemented all p08 masking modules and acceptance tests. Updated the typelint filter to match its absolute-path output and fail on any p08 diagnostic. Email masks use an invisible U+2060 word joiner so raw values matching the visible mask shape are still detected.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: None
**Hashes**: Source files were absent before implementation; final source and plan hashes are recorded in `tmp/telescreen-v1-20261008-s319/p08-masking/final-source-hashes.sha256`. Plan before=`d61cfa7c42b7ffb1212124c794627ef3ab2793c1d363d39c2472276e0b078d19`; previous plan hash before this final rationale update=`c260ffe547fd7c61e75009a1732c5e0e3ee8e4fd1922b914bb10453021803679`.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/masking tests/masking` -> exit 0, 9 files already formatted (log: `/tmp/telescreen-p08-masking-format-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/masking tests/masking` -> exit 0 (log: `/tmp/telescreen-p08-masking-ruff-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/masking tests/masking` -> exit 0 (log: `/tmp/telescreen-p08-masking-ty-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/masking 2>&1 | tee /tmp/telescreen-p08-masking-pytest-final.log` -> exit 0, 9 passed (complete log: `/tmp/telescreen-p08-masking-pytest-final.log`).
- `uv run --frozen python tools/typelint.py` plus unanchored p08 path filter -> p08 clean; full-tree typelint exits 1 with 47 diagnostics in other concurrent plans (log: `/tmp/telescreen-p08-masking-typelint-final.log`).
**Notes**: Final-source checks pass. Earlier complete logs record resolved attempts: `/tmp/telescreen-p08-masking-format.log` (exit 1, formatting), `/tmp/telescreen-p08-masking-ruff.log` (exit 1, lint), `/tmp/telescreen-p08-masking-ty.log` (exit 1, generic mismatch), `/tmp/telescreen-p08-masking-pytest.log` (exit 1, 7 passed/1 failed; email mask re-detected), `/tmp/telescreen-p08-masking-pytest-complete.log` (exit 1, 8 passed/1 failed; token fixture lacked a detector boundary), and `/tmp/telescreen-p08-masking-format-email.log` (exit 1, trailing blank line). Those issues were resolved and the current logs above are clean. No changes were made outside p08 writePaths. Formal test-integrity, adversarial and serial integration reviews remain downstream.

### Session: 2026-10-09 07:03 JST
**Tasks Completed**: Redispatch repair for p08 review feedback. Replaced the hard-coded retained-character counts with a measured longest-common-subsequence assertion across every sample. Added pre-handler log masking for message arguments, exceptions, stack text, custom `extra` fields, and string metadata; registered custom `Logger` subclasses are wrapped while preserving their class behavior. Added regressions for sensitive `extra` and logger-name fields on a custom logger with its own handler.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/masking/log_masking.py` before=`67ff178a31b294296e8db7e6f9ed83c77d98d6ae381ca060ca903cacb5e6094e` after=`96ae098691710fcabab556ef820041a5a174838f86199d8e348454cc1d840e56`; `tests/masking/test_masking.py` before=`8f56f941f51b6ab0587e50485e30f18714ef05a94174bff58f159660d68f38b1` after=`298dbbd8dcfda0549c855014bec6dd3e7e37203eb28f960f4b84300ec5e2ac87`; plan before=`58d49b49fb944ab37d9b37f0869c44e5562cd3a17e11b21b1c6a70c530cba3bf` (updated plan hash is in the refreshed hash manifest).
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/masking tests/masking` -> exit 0, 9 files already formatted (log: `tmp/telescreen-v1-20261008-s319/p08-masking/verification/final6/telescreen-p08-final6-format.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/masking tests/masking` -> exit 0 (log: `tmp/telescreen-v1-20261008-s319/p08-masking/verification/final6/telescreen-p08-final6-ruff.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/masking tests/masking` -> exit 0 (log: `tmp/telescreen-v1-20261008-s319/p08-masking/verification/final6/telescreen-p08-final6-ty.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/masking 2>&1 | tee /tmp/telescreen-p08-final6-pytest.log` -> exit 0, 9 passed, 0 failed (complete log: `tmp/telescreen-v1-20261008-s319/p08-masking/verification/final6/telescreen-p08-final6-pytest.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > /tmp/telescreen-p08-final6-typelint.log 2>&1; ! grep -E '(^|/)(src/telescreen/masking/|tests/masking/)' /tmp/telescreen-p08-final6-typelint.log` -> exit 0; full typelint reports 133 files clean and p08 filter is empty (logs: `tmp/telescreen-v1-20261008-s319/p08-masking/verification/final6/telescreen-p08-final6-typelint.log`, `tmp/telescreen-v1-20261008-s319/p08-masking/verification/final6/telescreen-p08-final6-typelint-filter.log`).
**Notes**: Test-integrity, adversarial and branch-evidence reviews all accept with no remaining findings; their joined typed decisions are recorded in `tmp/telescreen-v1-20261008-s319/p08-masking/reviews/native-join.json`. The final source hash manifest matches all authored p08 files. Intermediate review-repair attempts with incomplete format/lint/typing gates are preserved in `/tmp/telescreen-p08-final3-*`, `/tmp/telescreen-p08-custom-ty.log`, and the corresponding edit-intent records; final6 gates pass on the current source. Formal serial integration, staging, commit and push remain downstream.

### Session: 2026-10-09 07:12 JST
**Tasks Completed**: Addressed adversarial finding `P08-ADV-PRIVKEY-HEADERLESS-LEAK` (`comm-005438`). `mask_value` now preserves only a fully matched PEM header and otherwise emits a fixed private-key redaction; email spans without `@` use a fixed length-only mask. Added single-line and multiline headerless key invariants, empty key and existing PEM-format checks, malformed-email fallback, and a `Masker.seal` regression for `llm_private_key` from `DetectorKind.MANAGED_LLM`.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/masking/mask.py` before=`7b186b1994d7bf83464e51635b4a2d3d94d06aec98a9d5e349e48d8393dcda21` after=`f7ed07a1fe7e9bcbe9b5222ea0cb026f85b5e0ea0b2c13c2695a31c45ba84cbd`; `tests/masking/test_masking.py` before=`298dbbd8dcfda0549c855014bec6dd3e7e37203eb28f960f4b84300ec5e2ac87` after=`ee1a27861a5bba27706ac1dd2c0cd11e8ac58869e58d36b51fb3d41bac69f074`; plan before=`bc9fc241a910d8730df3ed9cc8f4b0d87d1918790ad441b672a1d9757cb76796`; updated plan hash is recorded in `tmp/telescreen-v1-20261008-s319/p08-masking/final-source-hashes.sha256`.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/masking tests/masking` -> exit 0, 9 files already formatted (log: `tmp/telescreen-v1-20261008-s319/p08-masking/verification/repair-comm005438/ruff-format-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/masking tests/masking` -> exit 0, all checks passed (log: `tmp/telescreen-v1-20261008-s319/p08-masking/verification/repair-comm005438/ruff-check-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/masking tests/masking` -> exit 0, all checks passed (log: `tmp/telescreen-v1-20261008-s319/p08-masking/verification/repair-comm005438/ty-check-final.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/masking 2>&1 | tee tmp/telescreen-v1-20261008-s319/p08-masking/verification/repair-comm005438/pytest-final.log` -> exit 0, 11 passed, 0 failed (complete log: `tmp/telescreen-v1-20261008-s319/p08-masking/verification/repair-comm005438/pytest-final.log`). The first fixture run had 1 LCS-marker coincidence failure and 10 passes; the adjusted base64-like fixture passed 11/11 (logs: `pytest.log`, `pytest-retry.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py > tmp/telescreen-v1-20261008-s319/p08-masking/verification/repair-comm005438/typelint-full-final.log 2>&1; ! grep -E '(^|/)(src/telescreen/masking/|tests/masking/)' tmp/telescreen-v1-20261008-s319/p08-masking/verification/repair-comm005438/typelint-full-final.log` -> exit 0; full linter reports 134 files clean and the p08 filter is empty (logs: `typelint-full-final.log`, `typelint-p08-filter-final.log`).
- Headerless key reproduction -> exit 0; printed `[REDACTED PRIVATE KEY 84 chars]`, `raw key in snippet: False`, and `********[4]` (log: `tmp/telescreen-v1-20261008-s319/p08-masking/verification/repair-comm005438/reproduction.log`).
**Notes**: `final-source-hashes.sha256` is refreshed after this entry. No files outside the p08 writePaths were changed. The corrected adversarial finding awaits independent re-review; combined-tree integration, staging, commit and push remain downstream.
