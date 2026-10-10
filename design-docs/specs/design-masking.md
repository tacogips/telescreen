# Masking, IndexGuard and Outbound DLP Gate

Data-handling guarantees: how raw sensitive values are masked, fingerprinted and kept out of
storage, logs, search, notifications, API responses and tracebacks.

## Overview

Raw values only exist in `ScanItem.text` (and evidence/upload bytes) inside the coroutine
that scans them. The masking pipeline is the only component allowed to read a span of raw text.
It emits `MaskedFinding` values that are safe to persist. Three rule-based guards provide
defense in depth: the masking log formatter, the IndexGuard before search indexing and the
DLP gate before outbound notifications. Police results and pushed findings are
re-scanned with the same rules.

## Details

### Components (`telescreen.masking`)

| Module | Responsibility |
|--------|----------------|
| `mask.py` | `mask_value(value, rule) -> str` per masking policy |
| `fingerprint.py` | `Fingerprinter` dataclass: `fingerprint(rule_id, raw)`, `resource_key(source, raw_locator)`; key and key id from settings |
| `seal.py` | `Masker.seal(item: ScanItem, result: DetectionResult) -> list[MaskedFinding]` |
| `redact.py` | `Redactor.redact(text) -> RedactionResult(text, hit_count, rule_classes)`: runs RuleDetector synchronously and masks every span |
| `index_guard.py` | `IndexGuard.check(document_fields) -> GuardVerdict` (rule-only) |
| `log_masking.py` | `MaskingFormatter`, `configure_logging(level)`, `install_excepthook()` |
| `safe_errors.py` | Helpers that render exceptions as `code` + error id only |

### Masking policy

| Rule class | Example output | Kept characters |
|------------|----------------|-----------------|
| credential/token with known prefix (AWS `AKIA`, GitHub `ghp_`, Slack `xoxb-`, Google `AIza`) | `ghp_****************[40]` | prefix only, plus length |
| private_key | `-----BEGIN PRIVATE KEY----- [REDACTED 1675 chars]` | header line only |
| jwt, password, generic secret | `********[24]` | none, length only |
| payment_card | `**** **** **** 1111` | last 4 digits |
| national_id (My Number) | `************` | none |
| email | `j***@example.com` | first local char, full domain |
| phone | `***-****-**78` | last 2 digits |
| address | `[ADDRESS REDACTED]` | none |

Invariant (property-tested): the masked output keeps at most 4 characters of the secret
part of a value and never more than 25% of the secret part's length. Two parts are not
secret and are excluded from the count:

- a fixed public type prefix from the closed list in the rule catalog (`AKIA`, `ASIA`,
  `ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`, `xoxa-`, `xoxb-`, `xoxp-`, `xoxr-`,
  `xoxs-`, `AIza`), and the PEM header line;
- the domain part of an email address, which is kept in full.

For emails the secret part is the local part, so the rule keeps its first character only.

### Fingerprints

- `fingerprint = hex(HMAC-SHA256(key, "v1\0" + rule_id + "\0" + normalized))`.
- Normalization: NFKC; numeric classes (card, My Number, phone) strip spaces and hyphens;
  email lowercased; others unchanged.
- Key from `TELESCREEN_FINGERPRINT_KEY` (secret ref, at least 32 bytes). `fingerprint_key_id`
  is the first 8 hex chars of `sha256(key)` and is stored next to each fingerprint so a key
  rotation is visible (old fingerprints are not re-keyed in v1).
- External (police/push) fingerprints are re-keyed:
  `HMAC(key, "ext\0" + source + "\0" + external_fingerprint)`.
- v1.1 key versioning (`design-v1-1-dspm-adoption.md` section 9):
  - The `Fingerprinter` is built from `TELESCREEN_FINGERPRINT_KEYS`
    (`<version>=<secret ref>` list; the legacy `TELESCREEN_FINGERPRINT_KEY` means version
    1) and holds the active key plus a separate identity key.
  - Version 1 is the identity key. It is always used for `resource_key`, placement keys,
    owner identity keys and the Drive channel token, so a rotation never changes issue
    identity.
  - The active version (`TELESCREEN_FINGERPRINT_ACTIVE_VERSION`, default highest) is used
    for value fingerprints and external re-keying. It is stored as
    `findings.fingerprint_key_version` next to `fingerprint_key_id`.
  - Old fingerprints are still not re-keyed, so linking across versions is not possible.

### Masked snippets

1. Take a window of 40 characters each side of the finding span in the transient text,
   clamped to line boundaries.
2. Mask every detected span inside the window (not only the current finding).
3. Run `Redactor` over the result. If any hit remains, the snippet becomes `[REDACTED]`.
4. Truncate to 200 characters.

### Logging and tracebacks

- `configure_logging()` installs `MaskingFormatter` on the root handler and on
  `uvicorn`, `uvicorn.error`, `uvicorn.access`, `httpx`, `sqlalchemy` loggers (propagating to
  root), and is called first by every CLI entrypoint. `httpx`/`httpcore`/`anthropic` loggers
  are set to WARNING.
- `MaskingFormatter.format` redacts the fully formatted record, including `formatException`
  and `formatStack` output, so arguments, exception messages and tracebacks are covered.
- `install_excepthook()` routes uncaught exceptions through the same formatter.
- Unhandled API exceptions return `{"error": {"code": "internal_error", "error_id": "<uuid>"}}`
  and log the masked traceback with the same error id.
- Dataclasses that hold raw content (`ScanItem.text`, ingest request segments, upload bytes)
  declare those fields with `repr=False`.

### API error echo prevention

- FastAPI `RequestValidationError` handler returns only `loc` and `type` for each error
  (never `input`, `ctx` or `msg`), status 422, code `validation_error`.
- Pydantic dataclass bodies that carry raw content mark fields `repr=False`.
- `TelescreenError` responses expose `code` and the static `detail` only.

### IndexGuard

- Runs `RuleDetector` (always rule-only, independent of the configured pipeline) on every
  string field of every search document before it reaches the `SearchBackend`.
- Any hit rejects the whole document: the document is not indexed, an audit record
  `index_guard.rejected` stores index name, document id, field names and rule ids (no
  values), and a masked warning is logged.
- Masked values (`*` runs and `[REDACTED ...]` markers) do not match rules by construction;
  this is covered by tests.

### DLP gate

- `DlpGate.inspect(message: OutboundMessage) -> CensoredMessage` runs the configured DLP
  detector list (`TELESCREEN_DLP_DETECTORS`, default `rule`) over subject and body.
- Policy `TELESCREEN_DLP_POLICY`: `mask` (default; hits are redacted, verdict `masked`) or
  `block` (any hit blocks the send, verdict `blocked`). A residual hit after masking always
  blocks.
- `CensoredMessage` is constructed only inside `telescreen.notifications`, either by `DlpGate`
  or by rehydrating a stored `notifications` row (module-private factories). The
  `SlackNotifier` and `EmailNotifier` adapters accept `CensoredMessage`, not strings.
- `NotificationSender.send(request)` does no network I/O. In the caller's transaction it:
  1. resolves recipients (only to validate them; resolved addresses are not stored);
  2. runs the DLP gate;
  3. inserts the `notifications` row with masked subject and body, the verdict and status
     `queued` (or `blocked`);
  4. appends the audit record (`notification.queued` or `notification.blocked`);
  5. for queued rows, enqueues a `notification_delivery` engine task with idempotency key
     `notify:<notification_id>`.

  A rollback of the caller's transaction removes the row, the audit record and the task
  together, so nothing is sent without a record.
- The `notification_delivery` task handler (notifications) loads the row and skips it unless the
  status is `queued`. It resolves recipients again, rehydrates the `CensoredMessage`, sends
  through the adapter, then sets `sent` (or `failed` after the task's last attempt) and
  audits the result. Retries use the queue backoff. Delivery is at-least-once, keyed by
  notification id. A crash between the provider accepting the message and the status update
  can cause one duplicate send. That duplicate carries the same masked content and is never
  unrecorded.

### Re-scan of inbound masked data

Police results, pushed findings (`/api/ingest/v1/findings`), human task comments, issue
comments, workflow definition sources and chat messages are inspected with `Redactor`:

| Input | On hit |
|-------|--------|
| Police result, pushed finding, workflow definition source | reject with code `raw_value_detected` (422) and audit |
| Comments, chat user messages, LLM free-text output | redact before storing or forwarding |

### Leak test matrix (assembly plan, `tests/leak/`)

Sample secrets are built at runtime from fragments (gitleaks-safe): AWS key id and secret,
GitHub token, Slack token, Google API key, PEM private key, JWT, password assignment, Luhn
valid card, checksum-valid My Number, email, Japanese mobile and landline numbers, Japanese
address. The tests run push ingestion, a collector scan with fake adapters, issue listing, search,
a workflow with notify (queued, then delivered by `notification_delivery`), text evidence
upload (rejected and accepted), police result submission (clean and raw), chat with a fake
LLM, and a forced exception. Image evidence cases use a fake `ImageEvidenceScanner`:

- rejected: the scanner reports `contains_sensitive`. Nothing is stored and the response
  carries rule classes only.
- accepted: a PNG and a JPEG whose metadata (EXIF GPS and author, PNG `tEXt`) contains
  sample secrets. The stored bytes no longer contain the metadata.
- scanner unavailable: 503 `evidence_scanner_unavailable` and nothing stored.
- scanner returns free text: the upload is rejected.

Assertions: no sample (or its separator-stripped variant) appears in any text column of
any table, captured log output, captured search documents, sent notification payloads,
stored evidence files, API response bodies, or rendered tracebacks.

v1 has no distributed tracing exporter. The "traces" sink of the requirement is therefore
the rendered traceback text and exception messages: the forced-exception flow output
captured through `install_excepthook` and the logging formatter, plus the 500 error
response body. Outbound HTTP request bodies captured by the test `MockTransport` (Slack,
police push, LLM) count as notification and egress sinks. The matrix runs the real
`build_container` and injects only these seams: SQLite sessions, the in-memory search
backend (Meilisearch unset), `MockTransport` (including canned LLM responses), and
`FakeClock`. Only the two cases that the HTTP seam cannot express may replace the
container's image scanner: scanner unavailable and free-text scanner output. The matrix
must not stub a service in a way that bypasses the production masking path.

## References

See `design-docs/references/README.md`.
