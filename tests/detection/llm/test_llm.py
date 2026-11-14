"""Offline tests for LLM and ML detection providers."""

import asyncio
import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import ModuleType
from typing import Protocol

import anthropic
import httpx
import pytest

from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import (
    DetectorKind,
    ErrorCode,
    ManagedLlmProvider,
    RuleClass,
)
from telescreen.detection import ml
from telescreen.detection.composite import CompositeDetector
from telescreen.detection.factory import build_detector
from telescreen.detection.harness import (
    LlmImagePart,
    LlmJsonRequest,
    LlmSpanDetector,
)
from telescreen.detection.managed_llm import (
    ClaudeLlmClient,
    OpenAiCompatibleClient,
)
from telescreen.detection.ml import EntitySpan, MlDetector
from telescreen.detection.rule_detector import RuleDetector
from telescreen.detection.types import DetectionResult, Detector, ScanItem


class ResponseMode(Protocol):
    """Generate a response body for a test LLM request."""

    def __call__(self, request: LlmJsonRequest) -> str:
        """Return model JSON text."""


@dataclass
class FakeLlmClient:
    """Count concurrent fake calls while returning configured JSON."""

    response_mode: ResponseMode
    delay: float = 0.0
    calls: int = 0
    active: int = 0
    max_active: int = 0
    requests: list[LlmJsonRequest] = field(default_factory=list)

    async def complete_json(self, request: LlmJsonRequest) -> str:
        """Return a deterministic body without network access."""
        self.requests.append(request)
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(self.delay)
        response: str = self.response_mode(request)
        self.active -= 1
        return response


@dataclass(frozen=True)
class FakeClassifier:
    """Return predetermined token labels for ML detector tests."""

    def classify(self, texts: Sequence[str]) -> list[list[EntitySpan]]:
        """Return supported and unsupported labels for every item."""
        return [
            [
                EntitySpan(0, 4, "EMAIL", 0.9),
                EntitySpan(5, 8, "PER", 0.99),
            ]
            for _ in texts
        ]


@dataclass(frozen=True)
class FakeTransformersPipeline:
    """Return aggregated entity groups through the real transformers adapter."""

    def __call__(self, texts: list[str]) -> list[list[ml._PipelineEntity]]:
        return [
            [
                ml._PipelineEntity(0, 6, "EMAIL", 0.98),
                ml._PipelineEntity(7, 8, "PER", 0.9),
            ]
            for _ in texts
        ]


@dataclass
class FakePipelineFactory:
    """Capture transformers pipeline configuration without importing the package."""

    selected_task: str = ""
    selected_model: str = ""
    selected_aggregation: str = ""

    def __call__(
        self, task: str, *, model: str, aggregation_strategy: str
    ) -> FakeTransformersPipeline:
        self.selected_task = task
        self.selected_model = model
        self.selected_aggregation = aggregation_strategy
        return FakeTransformersPipeline()


@dataclass(frozen=True)
class FakeEnv:
    """Read environment values from a fixed set of pairs."""

    values: tuple[tuple[str, str], ...]

    def get(self, key: str, /) -> str | None:
        """Return a configured environment value."""
        return next((value for name, value in self.values if name == key), None)


def item(item_id: str, text: str) -> ScanItem:
    """Create one transient text item."""
    return ScanItem(item_id, "test", "item", text)


def body_for_first_id(request: LlmJsonRequest) -> str:
    """Return valid empty span results for every request item id."""
    request_ids: list[str] = [
        line.split("]", maxsplit=1)[0][1:] for line in request.user.splitlines()
    ]
    return json.dumps({"items": [{"id": request_id, "spans": []} for request_id in request_ids]})


@pytest.mark.asyncio
async def test_llm_windows_rebase_and_deduplicate_overlap_spans() -> None:
    """Overlapping window hits map to one span in the original item."""

    def window_response(request: LlmJsonRequest) -> str:
        request_id: str = request.user.split("]", maxsplit=1)[0][1:]
        if request_id.endswith("w0"):
            start: int = 210
        elif request_id.endswith("w1"):
            start = 110
        else:
            return json.dumps({"items": [{"id": request_id, "spans": []}]})
        return json.dumps(
            {
                "items": [
                    {
                        "id": request_id,
                        "spans": [
                            {
                                "start": start,
                                "end": start + 6,
                                "rule_class": "credential",
                                "confidence": 0.91,
                            }
                        ],
                    }
                ]
            }
        )

    client: FakeLlmClient = FakeLlmClient(window_response)
    detector: LlmSpanDetector = LlmSpanDetector(
        DetectorKind.MANAGED_LLM, client, "test", 300, 20, 2
    )
    results: list[DetectionResult] = await detector.detect_batch(
        [item("doc", "a" * 210 + "needle" + "b" * 234)]
    )
    assert [(finding.span.start, finding.span.end) for finding in results[0].findings] == [
        (210, 216)
    ]
    assert results[0].findings[0].rule_class is RuleClass.CREDENTIAL
    system: str = client.requests[0].system
    assert "JSON object only" in system
    assert "items, id, spans, start, end, rule_class, and confidence" in system
    assert "0-based, end-exclusive character offsets" in system
    assert "after the '[id] ' prefix" in system


@pytest.mark.asyncio
async def test_llm_batches_item_limit_and_caps_concurrency() -> None:
    """Forty-five items form three requests and obey the concurrency limit."""
    client: FakeLlmClient = FakeLlmClient(body_for_first_id, delay=0.001)
    detector: LlmSpanDetector = LlmSpanDetector(DetectorKind.LOCAL_LLM, client, "test", 1000, 20, 2)
    results: list[DetectionResult] = await detector.detect_batch(
        [item(f"item-{index}", "safe") for index in range(45)]
    )
    assert len(results) == 45
    assert client.calls == 3
    assert client.max_active <= 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        '{"items":[{"id":"unknown","spans":[]}]}',
        'text before {"items":[]}',
    ],
)
async def test_invalid_llm_responses_retry_three_times_without_echo(
    response: str,
) -> None:
    """Unknown ids and extra text are retried then safely rejected."""
    client: FakeLlmClient = FakeLlmClient(lambda request: response)
    detector: LlmSpanDetector = LlmSpanDetector(DetectorKind.MANAGED_LLM, client, "test", 500, 5, 1)
    with pytest.raises(TelescreenError) as error:
        await detector.detect_batch([item("private-id", "x")])
    assert error.value.code is ErrorCode.LLM_OUTPUT_INVALID
    assert "private-id" not in str(error.value)
    assert client.calls == 3


@pytest.mark.asyncio
async def test_llm_drops_out_of_bounds_spans_and_unknown_classes() -> None:
    """Out-of-range and unsupported labels are discarded without failing the batch."""
    response: str = json.dumps(
        {
            "items": [
                {
                    "id": "i0",
                    "spans": [
                        {"start": 0, "end": 2, "rule_class": "credential", "confidence": 0.9},
                        {"start": 0, "end": 2, "rule_class": "made_up", "confidence": 0.9},
                    ],
                }
            ]
        }
    )
    client: FakeLlmClient = FakeLlmClient(lambda request: response)
    detector: LlmSpanDetector = LlmSpanDetector(DetectorKind.MANAGED_LLM, client, "test", 500, 5, 1)
    result: DetectionResult = (await detector.detect_batch([item("item", "x")]))[0]
    assert result.findings == ()


@pytest.mark.asyncio
async def test_openai_compatible_request_includes_auth_schema_and_image() -> None:
    """MockTransport observes the expected wire shape without opening a socket."""

    async def respond(request: httpx.Request) -> httpx.Response:
        payload: str = (await request.aread()).decode("utf-8")
        assert request.headers["Authorization"] == "Bearer test-key"
        assert "/v1/chat/completions" in str(request.url)
        assert "json_schema" in payload
        assert base64.b64encode(b"image").decode("ascii") in payload
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"items":[]}'}}]},
        )

    transport: httpx.MockTransport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        client: OpenAiCompatibleClient = OpenAiCompatibleClient(
            http, "https://example.test/v1", SecretValue("test-key")
        )
        request: LlmJsonRequest = LlmJsonRequest(
            "model",
            "system",
            "prompt",
            '{"type":"object"}',
            50,
            (LlmImagePart("image/png", b"image"),),
        )
        assert await client.complete_json(request) == '{"items":[]}'


@pytest.mark.asyncio
async def test_openai_provider_error_does_not_echo_prompt_or_body() -> None:
    """Provider error bodies are omitted from the public exception detail."""

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="prompt secret in provider body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client: OpenAiCompatibleClient = OpenAiCompatibleClient(http, "https://example.test/v1")
        request: LlmJsonRequest = LlmJsonRequest(
            "model", "system", "confidential prompt", '{"type":"object"}', 50
        )
        with pytest.raises(TelescreenError) as error:
            await client.complete_json(request)
    assert error.value.code is ErrorCode.PROVIDER_ERROR
    assert "confidential prompt" not in str(error.value)
    assert "provider body" not in str(error.value)


@pytest.mark.asyncio
async def test_claude_client_extracts_text_and_sends_base64_image() -> None:
    """Anthropic SDK uses its injected mock transport for text and image parts."""
    schema_text: str = '{"type":"object"}'

    async def respond(request: httpx.Request) -> httpx.Response:
        payload: str = (await request.aread()).decode("utf-8")
        assert base64.b64encode(b"image").decode("ascii") in payload
        assert json.loads(payload)["output_config"]["format"]["type"] == "json_schema"
        assert json.loads(payload)["output_config"]["format"]["schema"] == json.loads(schema_text)
        return httpx.Response(
            200,
            json={
                "id": "message-id",
                "type": "message",
                "role": "assistant",
                "model": "test-model",
                "content": [{"type": "text", "text": '{"items":[]}'}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        sdk: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic(
            api_key="test-key", http_client=http
        )
        client: ClaudeLlmClient = ClaudeLlmClient(sdk)
        request: LlmJsonRequest = LlmJsonRequest(
            "test-model",
            "system",
            "prompt",
            schema_text,
            50,
            (LlmImagePart("image/png", b"image"),),
        )
        assert await client.complete_json(request) == '{"items":[]}'


@pytest.mark.asyncio
async def test_ml_detector_maps_known_labels_and_skips_person_labels() -> None:
    """ML results expose only supported classes and valid character spans."""
    detector: MlDetector = MlDetector(FakeClassifier())
    result: DetectionResult = (await detector.detect_batch([item("item", "abcd xyz")]))[0]
    assert len(result.findings) == 1
    assert result.findings[0].rule_class is RuleClass.EMAIL
    assert result.findings[0].span.start == 0
    assert result.findings[0].span.end == 4


@pytest.mark.asyncio
async def test_transformers_loader_aggregates_entity_groups_and_excludes_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lazy pipeline maps aggregated EMAIL labels without importing transformers."""
    pipeline_factory: FakePipelineFactory = FakePipelineFactory()
    fake_module: ModuleType = ModuleType("transformers")
    fake_module.__dict__["pipeline"] = pipeline_factory

    def import_fake_module(name: str) -> ModuleType:
        assert name == "transformers"
        return fake_module

    monkeypatch.setattr(ml.importlib, "import_module", import_fake_module)
    classifier: ml.TokenClassifier = ml.load_transformers_classifier("test-model")
    results: list[DetectionResult] = await MlDetector(classifier).detect_batch(
        [item("item", "a@b.co x")]
    )

    assert pipeline_factory.selected_task == "token-classification"
    assert pipeline_factory.selected_model == "test-model"
    assert pipeline_factory.selected_aggregation == "simple"
    assert len(results[0].findings) == 1
    assert results[0].findings[0].rule_class is RuleClass.EMAIL
    assert (results[0].findings[0].span.start, results[0].findings[0].span.end) == (0, 6)


def test_transformers_loader_is_lazy_and_reports_a_safe_missing_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing optional transformers is translated to a static configuration error."""

    def unavailable(name: str) -> ModuleType:
        raise ImportError(name)

    monkeypatch.setattr(ml.importlib, "import_module", unavailable)
    with pytest.raises(ConfigError, match="requires the 'ml' extra"):
        ml.load_transformers_classifier("test-model")


def test_build_detector_selects_rule_and_rejects_missing_providers() -> None:
    """Factory requires configuration for explicitly selected optional backends."""
    settings: TelescreenSettings = TelescreenSettings()
    env: FakeEnv = FakeEnv(())
    http: httpx.AsyncClient = httpx.AsyncClient()
    try:
        assert isinstance(build_detector(settings, env, http, [DetectorKind.RULE]), RuleDetector)
        with pytest.raises(ConfigError):
            build_detector(settings, env, http, [DetectorKind.RULE, DetectorKind.MANAGED_LLM])
        with pytest.raises(ConfigError):
            build_detector(settings, env, http, [DetectorKind.ML])
        with pytest.raises(ConfigError):
            build_detector(settings, env, http, [DetectorKind.LOCAL_LLM])
    finally:
        asyncio.run(http.aclose())


def test_build_detector_uses_codex_and_preserves_requested_stage_order() -> None:
    """Configured Codex credentials produce a managed LLM stage after rules."""
    settings: TelescreenSettings = TelescreenSettings(
        managed_llm_provider=ManagedLlmProvider.CODEX,
        managed_llm_model="chosen-model",
        openai_api_key_ref="env:OPENAI_KEY",
    )
    env: FakeEnv = FakeEnv((("OPENAI_KEY", "test-key"),))
    http: httpx.AsyncClient = httpx.AsyncClient()
    try:
        detector: Detector = build_detector(
            settings, env, http, [DetectorKind.RULE, DetectorKind.MANAGED_LLM]
        )
        assert isinstance(detector, CompositeDetector)
        assert [stage.kind for stage in detector.stages] == [
            DetectorKind.RULE,
            DetectorKind.MANAGED_LLM,
        ]
    finally:
        asyncio.run(http.aclose())
