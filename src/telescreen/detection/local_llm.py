"""OpenAI-compatible local LLM client."""

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import DetectorKind
from telescreen.detection.harness import LlmClient, LlmJsonRequest, LlmSpanDetector
from telescreen.detection.managed_llm import OpenAiCompatibleClient
from telescreen.detection.types import DetectionResult, ScanItem


@dataclass(frozen=True)
class LocalLlmClient:
    """Use the common JSON-schema client against a local model endpoint."""

    http: httpx.AsyncClient
    base_url: str
    api_key: SecretValue | None = None

    async def complete_json(self, request: LlmJsonRequest) -> str:
        """Complete a local request without exposing provider response details."""
        return await OpenAiCompatibleClient(self.http, self.base_url, self.api_key).complete_json(
            request
        )


@dataclass(frozen=True)
class LocalLlmDetector:
    """Local-provider detector backed by the shared span harness."""

    client: LlmClient
    model: str
    max_chars: int
    max_items: int
    concurrency: int

    @property
    def kind(self) -> DetectorKind:
        """Return the local LLM detector kind."""
        return DetectorKind.LOCAL_LLM

    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]:
        """Delegate span extraction to the shared LLM harness."""
        detector: LlmSpanDetector = LlmSpanDetector(
            self.kind, self.client, self.model, self.max_chars, self.max_items, self.concurrency
        )
        return await detector.detect_batch(items)
