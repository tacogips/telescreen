# P08 Masking, Redactor, IndexGuard and Log Masking Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-masking.md (Components, Masking policy, Fingerprints, Masked snippets, Logging and tracebacks, IndexGuard)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

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
  - email: first local character, then `***@`, then the domain;
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
uv run --frozen python tools/typelint.py > /tmp/telescreen-p08-masking-typelint.log 2>&1; grep -E '^(src/telescreen/masking|tests/masking)' /tmp/telescreen-p08-masking-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] All modules and symbols above exist with the exact names
- [ ] The masking invariant property test covers every sample
- [ ] Log capture and traceback tests show no leaks
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
