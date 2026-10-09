# P09 LLM Harness, Managed/Local LLM and ML Detectors, Factory Implementation Plan

**Status**: Completed
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
uv run --frozen python tools/typelint.py src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm
```

## Completion Criteria

- [x] LlmClient, LlmSpanDetector, Claude/Codex/Local clients, MlDetector and the factory delivered with the exact names
- [x] No test imports transformers or opens a socket
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 22:25 JST
**Tasks Completed**: Implemented the batched LLM span harness, Claude/Codex/local clients, optional ML detector, configured detector factory, offline tests and scoped progress evidence.
**Tasks In Progress**: Formal test-integrity, adversarial and serial integration reviews are downstream workflow steps.
**Blockers**: None for p09 paths. The full shared-tree typelint invocation exits 1 for diagnostics in other plans; the p09 paths are clean.
**Reconciliation requests**: None.
**Hashes**:
- `src/telescreen/detection/harness.py` before=absent after=723995eb9e0c522be6eaca6577ecd32760a69656a27f6142d2847467a3689d32
- `src/telescreen/detection/managed_llm.py` before=absent after=e01ae3783c0de6f087988ab13f82b290860778166054bc811e173378d347eb83
- `src/telescreen/detection/local_llm.py` before=absent after=b928797f9d067a75e4c3ad31e8e02dd690abf04fc0c113b2287e4f9a40025ccf
- `src/telescreen/detection/ml.py` before=absent after=4b6c8febcd998f7e474fbc5a51fa054e6892193111ba4ac22a87d938065b9698
- `src/telescreen/detection/factory.py` before=absent after=af86c95a0c6a44cd290d698eadb6dd4face675e1759795872980466d046c9793
- `tests/detection/llm/test_llm.py` before=absent after=59cae4e48e7b1045094b35b9453eda833bd78999cbe2510b07ee89055684c60f
- `impl-plans/active/p09-detection-llm-ml.md` before=4ac6cd07f64320ce6c44b6751792871f4b43e86c7438818910e9c4adcd4b437f after=see `tmp/telescreen-v1-20261008-s319/p09-detection-llm-ml/final-source-hashes.sha256`
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0 (log: `/tmp/telescreen-p09-detection-llm-ml-format.log`)
- `uv run --frozen ruff check --no-cache src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0 (log: `/tmp/telescreen-p09-detection-llm-ml-ruff.log`)
- `uv run --frozen ty check src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0 (log: `/tmp/telescreen-p09-detection-llm-ml-ty.log`)
- `uv run --frozen pytest -p no:cacheprovider tests/detection/llm` -> exit 0, 12 passed (log: `/tmp/telescreen-p09-detection-llm-ml-pytest.log`)
- `uv run --frozen python tools/typelint.py src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0, 6 files clean (log: `/tmp/telescreen-p09-detection-llm-ml-typelint-scoped.log`)
- `uv run --frozen python tools/typelint.py` -> exit 1, 47 diagnostics in other plans (log: `/tmp/telescreen-p09-detection-llm-ml-typelint.log`); p09 path filter is empty.
- `if rg -n '^\s*(from|import)\s+transformers\b|httpx\.(get|post)\(|requests\.(get|post)\(' tests/detection/llm; then ...; else ...; fi` -> exit 0; no transformer imports or direct HTTP calls (log: `/tmp/telescreen-p09-detection-llm-ml-test-boundary.log`).
**Notes**: Verification ran on the shared fanout tree. No network calls were made. Review acceptance, shared documentation/index updates, staging and git publication remain downstream.

### Session: 2026-10-09 Step 6 redispatch
**Tasks Completed**: Confirmed p03 is accepted by the fanout dependency gate; revalidated the existing p09 implementation against the accepted detector design; reran scoped format, lint, type, behavioral and test-boundary checks. No source correction was required.
**Tasks In Progress**: Test-integrity, adversarial and branch-evidence review decisions are pending the downstream native review join.
**Blockers**: None for p09 implementation paths. Full typelint reports 69 diagnostics outside p09; the p09 path filter is empty.
**Reconciliation requests**: None.
**Hashes**:
- `src/telescreen/detection/harness.py` unchanged: `723995eb9e0c522be6eaca6577ecd32760a69656a27f6142d2847467a3689d32`
- `src/telescreen/detection/managed_llm.py` unchanged: `e01ae3783c0de6f087988ab13f82b290860778166054bc811e173378d347eb83`
- `src/telescreen/detection/local_llm.py` unchanged: `b928797f9d067a75e4c3ad31e8e02dd690abf04fc0c113b2287e4f9a40025ccf`
- `src/telescreen/detection/ml.py` unchanged: `4b6c8febcd998f7e474fbc5a51fa054e6892193111ba4ac22a87d938065b9698`
- `src/telescreen/detection/factory.py` unchanged: `af86c95a0c6a44cd290d698eadb6dd4face675e1759795872980466d046c9793`
- `tests/detection/llm/test_llm.py` unchanged: `59cae4e48e7b1045094b35b9453eda833bd78999cbe2510b07ee89055684c60f`
- `impl-plans/active/p09-detection-llm-ml.md` before=`065fd32b4ff657b14089ed4c6e9282d02cad10c172f53e954cedad6e1dfc8c98`; after=recorded in the Step 6 result.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0 (log: `/tmp/telescreen-p09-rerun-format.log`)
- `uv run --frozen ruff check --no-cache src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0 (log: `/tmp/telescreen-p09-rerun-ruff.log`)
- `uv run --frozen ty check src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0 (log: `/tmp/telescreen-p09-rerun-ty.log`)
- `uv run --frozen pytest -p no:cacheprovider tests/detection/llm` -> exit 0, 12 passed (log: `/tmp/telescreen-p09-rerun-pytest-final.log`)
- `uv run --frozen python tools/typelint.py` -> exit 1, 69 diagnostics outside p09; p09 path filter empty (logs: `/tmp/telescreen-p09-rerun-typelint.log`, `/tmp/telescreen-p09-rerun-typelint-filter.log`)
- `rg` import/socket/network-call boundary check over `tests/detection/llm` -> exit 0, no prohibited imports or direct calls (log: `/tmp/telescreen-p09-rerun-boundary.log`)
**Notes**: The `DISPATCH3-REVIEW-MISSING-p09` finding identifies the missing native review join; this implementation step does not claim integrity, adversarial or branch-evidence review acceptance. Review acceptance, shared documentation/index updates, staging and git publication remain downstream.

### Session: 2026-10-09 adversarial repair
**Tasks Completed**: Fixed `ADV-CLAUDE-NO-OUTPUT-CONTRACT-p09` by defining the JSON fields and span offsets in the harness prompt, passing the schema through Anthropic structured output, removing unsupported numeric schema bounds while retaining runtime confidence checks, and adding wire/prompt assertions. Fixed `ADV-ML-BIO-LABELS-p09` by using simple aggregation and parsing `entity_group`; added an offline fake-module loader regression test.
**Tasks In Progress**: Independent re-review and branch-evidence review are pending downstream.
**Blockers**: None for p09. Latest full-tree typelint exits 1 on a TL000 parse diagnostic at `tests/collectors/google/test_sweep.py:207`, outside p09 and owned by the parallel Google collectors plan.
**Reconciliation requests**: Serial reconciliation should retain the unanchored p09 path filter; the prior anchored filter was vacuous for absolute paths.
**Hashes**:
- `src/telescreen/detection/harness.py` before=`723995eb9e0c522be6eaca6577ecd32760a69656a27f6142d2847467a3689d32` after=`30b041b6468dc8b6cccb50b888e96ce4a97c39f6d48b9f674f42a8208f2fecb8`
- `src/telescreen/detection/managed_llm.py` before=`e01ae3783c0de6f087988ab13f82b290860778166054bc811e173378d347eb83` after=`6f52bf8041908114254e1f4cafed6eade68bb88fd823c6d515dbedae47feb4c9`
- `src/telescreen/detection/ml.py` before=`4b6c8febcd998f7e474fbc5a51fa054e6892193111ba4ac22a87d938065b9698` after=`94434f53938afad34560d432f6001e21775db2531fdb93862f807ad3102ec63e`
- `tests/detection/llm/test_llm.py` before=`59cae4e48e7b1045094b35b9453eda833bd78999cbe2510b07ee89055684c60f` after=`f53eea215e5041d9ee19df4f54f446ae1ac9d3aea3f78650fe2222ddd437741c`
- `src/telescreen/detection/local_llm.py` unchanged=`b928797f9d067a75e4c3ad31e8e02dd690abf04fc0c113b2287e4f9a40025ccf`; `src/telescreen/detection/factory.py` unchanged=`af86c95a0c6a44cd290d698eadb6dd4face675e1759795872980466d046c9793`
- `impl-plans/active/p09-detection-llm-ml.md` before=`c0d3081060ee853f9fa8832bdd6fbbc2f11b051ef5a7a8fe60e1d440df41e1de`; after=recorded in the Step 6 result.
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0 (log: `/tmp/telescreen-p09-repair-format-final3.log`)
- `uv run --frozen ruff check --no-cache src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0 (log: `/tmp/telescreen-p09-repair-ruff-final3.log`)
- `uv run --frozen ty check src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0 (log: `/tmp/telescreen-p09-repair-ty-final3.log`)
- `uv run --frozen python tools/typelint.py src/telescreen/detection/harness.py src/telescreen/detection/managed_llm.py src/telescreen/detection/local_llm.py src/telescreen/detection/ml.py src/telescreen/detection/factory.py tests/detection/llm` -> exit 0, six files clean (log: `/tmp/telescreen-p09-repair-typelint-scoped-final3.log`)
- `uv run --frozen pytest -p no:cacheprovider tests/detection/llm` -> exit 0, 13 passed (log: `/tmp/telescreen-p09-repair-pytest-final3.log`)
- `uv run --frozen python tools/typelint.py` -> exit 1, one TL000 parse diagnostic in `tests/collectors/google/test_sweep.py:207`; the unanchored p09 filter is empty (logs: `/tmp/telescreen-p09-repair-typelint-full-final3.log`, `/tmp/telescreen-p09-repair-typelint-p09-filter-final3.log`)
- `rg` transformer/socket import and direct socket creation check over `tests/detection/llm` -> exit 0, no matches (log: `/tmp/telescreen-p09-repair-test-boundary-final3.log`)
- `uv run --frozen python tools/typelint.py <p09 paths>` -> an initial exit 1 for an untyped test binding was corrected; `ty` initially reported a ModuleType fake-attribute diagnostic, corrected by assigning through `__dict__`. Final scoped exits are 0.
**Notes**: The Anthropic schema omits unsupported `minimum`/`maximum` keywords per the [Anthropic Structured Outputs documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs); local validation still rejects confidence outside [0,1]. No real provider or transformers dependency was used. The adversarial review remains `needs_revision` until independent re-review; no approval is claimed.
