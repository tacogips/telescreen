# Detection Detectors

One batched detector interface with rule, ML, managed-LLM and local-LLM backends, selected
by configuration.

## Overview

All detectors implement `Detector.detect_batch`. Detectors return spans and labels, never
values. The masking pipeline converts spans into masked findings. Rule detection is local,
deterministic and always available; it is also the engine behind the Redactor, IndexGuard
and DLP gate. LLM and ML detectors are opt-in because they either send content to a provider
or need heavy optional dependencies.

## Details

### Types (`telescreen.detection.types`)

| Type | Fields |
|------|--------|
| `ScanItem` | `item_id: str`, `source: str`, `locator: str` (segment locator, e.g. line/cell/message), `text: str` (`repr=False`), `content_type: str` |
| `Span` | `start: int`, `end: int` (offsets into original `text`) |
| `RuleClass` (StrEnum, defined in `telescreen.core.vocabulary`) | `credential`, `token`, `private_key`, `password`, `payment_card`, `national_id`, `email`, `phone`, `address` |
| `Severity` (StrEnum, `telescreen.core.vocabulary`) | `high` (credential, token, private_key, password, payment_card, national_id), `medium` (email, phone, address) |
| `DetectorKind` (StrEnum, `telescreen.core.vocabulary`) | `rule`, `ml`, `managed_llm`, `local_llm`, `police` |
| `RawFinding` | `rule_id: str`, `rule_class: RuleClass`, `span: Span`, `confidence: float`, `detector: DetectorKind` |
| `DetectionResult` | `item_id: str`, `findings: tuple[RawFinding, ...]`, `degraded: bool` |

```python
class Detector(Protocol):
    kind: DetectorKind
    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]: ...
```

Contract: one `DetectionResult` per input item, same order; spans lie within the text;
`degraded=True` when a backend failed and the result came from fallback.

### Rule detector

- Text is normalized with per-character NFKC and an index map back to original offsets, so
  full-width digits and letters match and spans stay correct.
- Each rule: id, class, regex, optional validator (checksum, entropy, decoding), optional
  context keywords, base confidence.

| Rule id | Class | Detection | Validator |
|---------|-------|-----------|-----------|
| `aws_access_key_id` | credential | `(AKIA|ASIA)[A-Z0-9]{16}` | - |
| `aws_secret_access_key` | credential | 40 chars base64 set near `aws_secret`/`secret_access_key` | Shannon entropy >= 4.0 |
| `github_token` | token | `gh[pousr]_[A-Za-z0-9]{36,255}`, `github_pat_[A-Za-z0-9_]{82}` | - |
| `slack_token` | token | `xox[abprs]-[A-Za-z0-9-]{10,}`; Slack webhook URL | - |
| `google_api_key` | credential | `AIza[0-9A-Za-z_-]{35}` | - |
| `private_key` | private_key | `-----BEGIN (RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----` through END line | - |
| `jwt` | token | three base64url segments starting `eyJ` | header decodes to JSON with `alg` |
| `password_assignment` | password | `(password|passwd|pwd|secret|pass|パスワード)\s*[:=]\s*value` | value length >= 8, not a placeholder (`***`, `${...}`, `<...>`, `changeme`, `example`) |
| `credit_card` | payment_card | 13-19 digits with optional space/hyphen groups | Luhn and known IIN prefix |
| `my_number` | national_id | 12 digits, optional `4-4-4` grouping, word boundaries | My Number check digit |
| `email` | email | RFC 5322 subset | TLD present |
| `phone_jp` | phone | `0\d{1,4}-\d{1,4}-\d{4}`, `0[5789]0-?\d{4}-?\d{4}`, `+81` forms, full-width via NFKC | digit count 10-11 |
| `phone_intl` | phone | `+<cc>` E.164-like with separators | 8-15 digits |
| `address_jp` | address | prefecture name followed by city/ward/town and block numbers (`丁目`, `番地`, `-` digits), or `〒\d{3}-\d{4}` with following text | confidence 0.6 |
| `address_en` | address | house number + street name + street suffix | confidence 0.5 |

Rule detection is synchronous internally (`RuleDetector.scan_text(text) -> list[RawFinding]`)
and wrapped by the async `detect_batch`. Overlapping findings keep the highest confidence;
ties prefer `high` severity.

### LLM harness (`telescreen.detection.harness`)

```python
class LlmClient(Protocol):
    async def complete_json(self, request: LlmJsonRequest) -> str: ...
```

- `LlmJsonRequest`: model, system prompt, user prompt, JSON schema text, max tokens.
- Managed LLM provider (user decision, `design-docs/user-qa/qa-llm-data-egress.md`):
  `TELESCREEN_MANAGED_LLM_PROVIDER` = `claude` (default) | `codex`.
  - `claude`: `ClaudeLlmClient` uses `anthropic.AsyncAnthropic` (default model
    `claude-sonnet-5-5`, `TELESCREEN_MANAGED_LLM_MODEL`, key `TELESCREEN_ANTHROPIC_API_KEY`
    secret ref).
  - `codex`: `CodexLlmClient` uses the OpenAI API (`TELESCREEN_OPENAI_API_KEY` secret ref,
    `TELESCREEN_OPENAI_BASE_URL` default `https://api.openai.com/v1`); the model must be set
    explicitly in `TELESCREEN_MANAGED_LLM_MODEL` (no baked-in default). It shares the
    OpenAI-compatible request/response code with `LocalLlmClient` (httpx, JSON-schema
    `response_format`), adding auth headers only.
  - Both implement `LlmClient`; `LlmJsonRequest` carries optional image parts
    (`LlmImagePart(media_type, data: bytes)`) so the same clients serve evidence image
    scanning (`ImageEvidenceScanner`) and Assistant chat.
- `LocalLlmClient` uses httpx against an OpenAI-compatible `/v1/chat/completions`
  (`TELESCREEN_LOCAL_LLM_BASE_URL`, `TELESCREEN_LOCAL_LLM_MODEL`) with `response_format`
  JSON schema.
- Prompt: items are numbered; each item's text is given with an id; the model must return
  `{"items": [{"id": ..., "spans": [{"start", "end", "rule_class", "confidence"}]}]}`. The
  prompt instructs the model to never repeat content.
- Output validation: parse with `TypeAdapter.validate_json`; drop spans outside bounds or
  with unknown classes; reject the whole response when extra free text or unknown ids
  appear; retry up to 2 times; then fail the batch.
- Batching: pack items up to `TELESCREEN_LLM_BATCH_MAX_CHARS` (default 12000) and
  `TELESCREEN_LLM_BATCH_MAX_ITEMS` (default 20); items longer than the char limit are split
  into overlapping windows (200 char overlap) and spans are re-based. Concurrency limited by
  `TELESCREEN_LLM_CONCURRENCY` (default 4).
- Error texts from providers are logged only through the masking formatter and never
  include prompt text.
- `ManagedLlmDetector` and `LocalLlmDetector` are thin detectors over this harness.

### ML detector

`MlDetector` wraps a token-classification pipeline behind
`TokenClassifier` Protocol (`classify(texts) -> list[list[EntitySpan]]`), run in a worker
thread. The transformers implementation is imported lazily from the optional extra `ml`
(`TELESCREEN_ML_MODEL` path or hub id). If the import fails, selecting `ml` raises a
configuration error at startup (exit code 3). Labels are mapped to `RuleClass` by a
configurable label map (default: PER/NAME excluded, EMAIL, PHONE, ADDRESS, CREDENTIAL).
Tests use a fake classifier; transformers is never imported in tests.

### Composite and configuration

| Setting | Values | Default |
|---------|--------|---------|
| `TELESCREEN_DETECTORS` | comma list of `rule`, `ml`, `managed_llm`, `local_llm` | `rule` |
| `TELESCREEN_DETECTOR_MODE` | `union`, `cascade` | `union` |
| `TELESCREEN_DLP_DETECTORS` | same list | `rule` |

- `union`: all detectors run on all items; overlapping spans merged.
- `cascade`: the first detector produces candidates; following detectors receive only items
  with candidates, scoped to 300-char windows around candidates; candidates with
  confidence >= 0.9 from exact-format rules (credential, token, private_key, payment_card,
  national_id) are kept regardless; other candidates are kept only if a later detector
  confirms an overlapping span.
- A failing non-rule stage marks results `degraded=True` and falls back to the previous
  stage's findings; it never drops rule findings.
- `build_detector(settings, names) -> Detector` in `factory.py` is the only constructor
  used by the rest of the system. `build_managed_llm_client(settings) -> LlmClient | None`
  is the only constructor for the managed provider client. It returns None when the
  selected provider has no credentials. Evidence's `LlmImageEvidenceScanner` and Assistant
  chat use it (wired in assembly). Workflow `detect` nodes may pass an explicit list.

### Batching for scans

The scan pipeline groups `ScanItem`s into batches of `TELESCREEN_SCAN_BATCH_ITEMS` (default
200) per call to the pipeline; content is segmented by line (files), cell (sheets) or
message (Slack) so locators are precise.

## References

See `design-docs/references/README.md`.
