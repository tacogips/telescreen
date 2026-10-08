# P09 LLM Harness, Managed/Local LLM and ML Detectors, Factory Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/design-detectors.md (LLM harness, ML detector, Composite and configuration), design-docs/user-qa/qa-llm-data-egress.md
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p09-detection-llm-ml`
- planPath: `impl-plans/active/p09-detection-llm-ml.md`
- wave: 3
- dependsOn: `p03-detection-rules`
- writePaths:
  - `impl-plans/active/p09-detection-llm-ml.md`
  - `src/telescreen/detection/harness.py`
  - `src/telescreen/detection/managed_llm.py`
  - `src/telescreen/detection/local_llm.py`
  - `src/telescreen/detection/ml.py`
  - `src/telescreen/detection/factory.py`
  - `tests/detection/llm`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

This plan delivers:

- the four switchable detectors behind the one `Detector` interface (rule and composite
  come from p03; this plan adds the managed LLM (Claude default, or Codex through the
  OpenAI API), the local LLM (OpenAI-compatible) and the ML detector);
- the shared `LlmClient`, which evidence image scanning (p18), Assistant chat (p27) and
  `ai_agent` steps (p25) reuse;
- the factory, which is the only constructor the rest of the system uses.

## Non-goals

- No network in tests. transformers is never imported in tests.
- No chat tool loop (p27) and no image evidence scanner (p18); only the client support
  for image parts.

## Deliverables

### `harness.py`
- `LlmImagePart(media_type: str, data: bytes = field(repr=False))`.
- `LlmJsonRequest(model: str, system: str, user: str = field(repr=False), json_schema: str, max_tokens: int, images: tuple[LlmImagePart, ...] = ())`.
- Protocol:

```python
class LlmClient(Protocol):
    async def complete_json(self, request: LlmJsonRequest) -> str: ...
```

  Returns the model's JSON text. Raises `TelescreenError(provider_error)` with a static
  detail. Prompt text never appears in errors or logs.
- `LlmSpanDetector(kind: DetectorKind, client: LlmClient, model: str, max_chars: int, max_items: int, concurrency: int)`
  implements `Detector`:
  - Batching: pack items until `max_chars` or `max_items` is reached. An item longer
    than `max_chars` is split into windows with 200-character overlap; spans are re-based
    to the item and duplicates from overlaps are merged.
  - Concurrency is limited by `asyncio.Semaphore(concurrency)`.
  - Prompt: a system prompt says "return only spans, never repeat content". The user
    prompt numbers the items as `[id] text`.
  - The schema is `{"items":[{"id": str, "spans":[{"start": int, "end": int, "rule_class": enum, "confidence": number}]}]}`.
  - Validation: parse with `TypeAdapter(LlmSpanResponse).validate_json(text)` into
    dataclasses. Drop spans that are out of bounds or have an unknown class. An unknown
    id or extra free text (a parse failure) rejects the whole response.
  - Retry twice, then raise `TelescreenError(llm_output_invalid)`.
  - Findings get `detector = kind` and the returned confidence.
  - Results go through `check_results`.

### `managed_llm.py`
- `ClaudeLlmClient(client: anthropic.AsyncAnthropic)` uses `messages.create` with the
  system prompt, a text block and base64 image blocks for `images`. The instructions
  require JSON-only output; return the concatenated text of the text blocks.
- `CodexLlmClient(http: httpx.AsyncClient, base_url: str, api_key: SecretValue)` and
  `LocalLlmClient` share `OpenAiCompatibleClient(http, base_url, api_key: SecretValue | None)`.
  It POSTs `/chat/completions` with `response_format: {"type":"json_schema","json_schema":{"name":"result","schema": <inline json.loads(schema)>,"strict": true}}`
  and images as `{"type":"image_url","image_url":{"url":"data:<mime>;base64,..."}}`.
  Parse with `TypeAdapter.validate_json(response.content)` into a minimal response
  dataclass and return `choices[0].message.content`.
- `ManagedLlmDetector` and `LocalLlmDetector` are thin factories returning an
  `LlmSpanDetector` with `kind` `managed_llm` or `local_llm`.

### `ml.py`
- `EntitySpan(start: int, end: int, label: str, score: float)`.
- `class TokenClassifier(Protocol): def classify(self, texts: Sequence[str]) -> list[list[EntitySpan]]`.
- `MlDetector(classifier: TokenClassifier, label_map: tuple[tuple[str, RuleClass], ...])`
  runs `classify` via `asyncio.to_thread`. The default label map is EMAIL -> email,
  PHONE -> phone, ADDRESS -> address, CREDENTIAL -> credential; PER and NAME are excluded.
- `load_transformers_classifier(model: str) -> TokenClassifier` imports transformers
  lazily inside the function. An `ImportError` raises
  `ConfigError("ml detector requires the 'ml' extra")`.

### `factory.py`
- `build_managed_llm_client(settings: TelescreenSettings, env: EnvSource, http: httpx.AsyncClient) -> LlmClient | None`:
  - `claude` with an Anthropic key ref -> `ClaudeLlmClient(AsyncAnthropic(api_key=..., http_client=http))`;
  - `codex` with an OpenAI key ref -> `CodexLlmClient`;
  - no key ref -> `None`.
  - Model from `settings.managed_llm_model`.
- `build_local_llm_client(settings, env, http) -> LlmClient | None`.
- `build_detector(settings, env, http, names: Sequence[DetectorKind], classifier: TokenClassifier | None = None) -> Detector`:
  - a single `rule` -> `RuleDetector`;
  - otherwise `CompositeDetector` in `settings.detector_mode` order;
  - `managed_llm` without a client, `local_llm` without a base URL and model, or `ml`
    without a model raises `ConfigError` (exit code 3 at the CLI).

## Key Pitfalls

- Do not log provider error bodies verbatim (they may echo prompt text). Log the HTTP
  status code and the error code only.
- The anthropic SDK accepts `http_client=`; tests inject
  `httpx.AsyncClient(transport=httpx.MockTransport(handler))`.
- `json_schema` travels as text inside `LlmJsonRequest`. Turn it into a mapping only
  inline in the outgoing request body (`json.loads` inline), never bound to a variable.
- Window merge: findings from overlapping windows covering the same original span count
  once.

## Tests (`tests/detection/llm/`)

- A fake `LlmClient` returning valid spans for numbered items -> findings with correct
  re-based spans across a split long item (window boundary case).
- Responses with an unknown id, with text before the JSON, or with an out-of-bounds span:
  out-of-bounds spans are dropped; the others retry and finally raise
  `llm_output_invalid`; the client is called 3 times.
- Batching: 45 items with `max_items=20` -> 3 client calls; concurrency is never above
  `concurrency` (track in the fake).
- `OpenAiCompatibleClient` with MockTransport: the request has the bearer header, a
  `response_format` json_schema and an image data URL; the returned content is passed
  through. A 500 response -> `provider_error`, and the message contains neither the
  prompt nor the response body.
- `ClaudeLlmClient` with MockTransport returning a messages API JSON body -> text
  extracted; the request contains a base64 image block when images are given.
- `MlDetector` with a fake classifier maps labels and skips PER.
- `build_detector` with names `rule` -> RuleDetector; with `rule,managed_llm` and no key
  -> ConfigError; with codex and a key -> a composite whose second stage kind is
  `managed_llm`; `ml` without a model -> ConfigError.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/detection/llm 2>&1 | tee /tmp/telescreen-p09-detection-llm-ml-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p09-detection-llm-ml-typelint.log 2>&1; grep -E '^(src/telescreen/detection/(harness|managed_llm|local_llm|ml|factory)\.py|tests/detection/llm)' /tmp/telescreen-p09-detection-llm-ml-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] LlmClient, LlmSpanDetector, Claude/Codex/Local clients, MlDetector and the factory delivered with the exact names
- [ ] No test imports transformers or opens a socket
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
