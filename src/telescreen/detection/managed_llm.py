"""Managed Claude and OpenAI-compatible LLM clients."""

import base64
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import anthropic
import httpx
from pydantic import TypeAdapter, ValidationError

from telescreen.core.errors import TelescreenError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import DetectorKind, ErrorCode
from telescreen.detection.harness import LlmClient, LlmJsonRequest, LlmSpanDetector
from telescreen.detection.types import DetectionResult, ScanItem


@dataclass(frozen=True)
class _OpenAiMessage:
    """Minimal OpenAI-compatible response message."""

    content: str | None


@dataclass(frozen=True)
class _OpenAiChoice:
    """Minimal OpenAI-compatible response choice."""

    message: _OpenAiMessage


@dataclass(frozen=True)
class _OpenAiResponse:
    """Minimal OpenAI-compatible completion response."""

    choices: tuple[_OpenAiChoice, ...]


_OPENAI_RESPONSE_ADAPTER: TypeAdapter[_OpenAiResponse] = TypeAdapter(_OpenAiResponse)


def _anthropic_media_type(
    media_type: str,
) -> Literal["image/jpeg", "image/png", "image/gif", "image/webp"]:
    """Validate the image media type accepted by Anthropic's SDK contract."""
    match media_type:
        case "image/jpeg":
            return "image/jpeg"
        case "image/png":
            return "image/png"
        case "image/gif":
            return "image/gif"
        case "image/webp":
            return "image/webp"
        case _:
            raise TelescreenError(ErrorCode.PROVIDER_ERROR, "LLM image type is unsupported")


@dataclass(frozen=True)
class OpenAiCompatibleClient:
    """Send JSON-schema constrained requests to an OpenAI-compatible endpoint."""

    http: httpx.AsyncClient
    base_url: str
    api_key: SecretValue | None = None

    async def complete_json(self, request: LlmJsonRequest) -> str:
        """Return completion text without exposing provider error bodies."""
        headers: list[tuple[str, str]] = []
        if self.api_key is not None:
            headers.append(("Authorization", f"Bearer {self.api_key.value}"))
        content: bytes
        try:
            response: httpx.Response = await self.http.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": request.model,
                    "messages": [
                        {"role": "system", "content": request.system},
                        {
                            "role": "user",
                            "content": request.user
                            if not request.images
                            else [
                                {"type": "text", "text": request.user},
                                *[
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": (
                                                f"data:{image.media_type};base64,"
                                                f"{base64.b64encode(image.data).decode('ascii')}"
                                            )
                                        },
                                    }
                                    for image in request.images
                                ],
                            ],
                        },
                    ],
                    "max_tokens": request.max_tokens,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "result",
                            "schema": json.loads(request.json_schema),
                            "strict": True,
                        },
                    },
                },
            )
            if response.status_code >= 400:
                raise TelescreenError(ErrorCode.PROVIDER_ERROR, "LLM provider request failed")
            content = response.content
        except TelescreenError:
            raise
        except httpx.HTTPError:
            raise TelescreenError(ErrorCode.PROVIDER_ERROR, "LLM provider request failed") from None
        try:
            parsed: _OpenAiResponse = _OPENAI_RESPONSE_ADAPTER.validate_json(content)
            text: str | None = parsed.choices[0].message.content if parsed.choices else None
        except (ValidationError, IndexError):
            raise TelescreenError(
                ErrorCode.PROVIDER_ERROR, "LLM provider response is invalid"
            ) from None
        if text is None:
            raise TelescreenError(ErrorCode.PROVIDER_ERROR, "LLM provider response is invalid")
        return text


@dataclass(frozen=True)
class CodexLlmClient:
    """Managed OpenAI API client used when Codex is selected."""

    http: httpx.AsyncClient
    base_url: str
    api_key: SecretValue

    async def complete_json(self, request: LlmJsonRequest) -> str:
        """Complete a request through the shared OpenAI-compatible client."""
        return await OpenAiCompatibleClient(self.http, self.base_url, self.api_key).complete_json(
            request
        )


@dataclass(frozen=True)
class ClaudeLlmClient:
    """Managed Anthropic client with image-capable JSON output."""

    client: anthropic.AsyncAnthropic

    async def complete_json(self, request: LlmJsonRequest) -> str:
        """Return concatenated text blocks from an Anthropic message."""
        content: list[anthropic.types.TextBlockParam | anthropic.types.ImageBlockParam] = [
            {"type": "text", "text": request.user}
        ]
        for image in request.images:
            source: anthropic.types.Base64ImageSourceParam = {
                "type": "base64",
                "media_type": _anthropic_media_type(image.media_type),
                "data": base64.b64encode(image.data).decode("ascii"),
            }
            image_part: anthropic.types.ImageBlockParam = {
                "type": "image",
                "source": source,
            }
            content.append(image_part)
        try:
            response: anthropic.types.Message = await self.client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": json.loads(request.json_schema),
                    }
                },
            )
        except anthropic.APIError:
            raise TelescreenError(ErrorCode.PROVIDER_ERROR, "LLM provider request failed") from None
        return "".join(
            block.text for block in response.content if isinstance(block, anthropic.types.TextBlock)
        )


@dataclass(frozen=True)
class ManagedLlmDetector:
    """Managed-provider detector backed by the shared span harness."""

    client: LlmClient
    model: str
    max_chars: int
    max_items: int
    concurrency: int

    @property
    def kind(self) -> DetectorKind:
        """Return the managed LLM detector kind."""
        return DetectorKind.MANAGED_LLM

    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]:
        """Delegate span extraction to the shared LLM harness."""
        detector: LlmSpanDetector = LlmSpanDetector(
            self.kind, self.client, self.model, self.max_chars, self.max_items, self.concurrency
        )
        return await detector.detect_batch(items)
