"""Fail-closed vision scanning for image evidence."""

import json
from dataclasses import dataclass
from typing import Protocol

import pydantic
from pydantic import TypeAdapter, ValidationError

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, RuleClass
from telescreen.detection.harness import LlmClient, LlmImagePart, LlmJsonRequest


@dataclass(frozen=True)
class ImageVerdict:
    """A safe classifier verdict that contains no image text."""

    contains_sensitive: bool
    rule_classes: tuple[RuleClass, ...]


class ImageEvidenceScanner(Protocol):
    """Scan an image for sensitive content without transcribing it."""

    async def scan(self, content_type: str, data: bytes) -> ImageVerdict:
        """Return only a sensitivity verdict and rule classes."""


@pydantic.dataclasses.dataclass(config=pydantic.ConfigDict(extra="forbid"))
class _ImageResponse:
    """Strict JSON response accepted from the vision model."""

    contains_sensitive: bool
    rule_classes: tuple[RuleClass, ...]


_RESPONSE_ADAPTER: TypeAdapter[_ImageResponse] = TypeAdapter(_ImageResponse)
_JSON_SCHEMA: str = json.dumps(
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["contains_sensitive", "rule_classes"],
        "properties": {
            "contains_sensitive": {"type": "boolean"},
            "rule_classes": {
                "type": "array",
                "items": {"type": "string", "enum": [item.value for item in RuleClass]},
            },
        },
    },
    separators=(",", ":"),
)


@dataclass(frozen=True)
class LlmImageEvidenceScanner:
    """Use a JSON-only managed LLM request to classify transient image bytes."""

    client: LlmClient
    model: str

    async def scan(self, content_type: str, data: bytes) -> ImageVerdict:
        """Request a strict verdict, retrying provider and output errors twice."""
        request: LlmJsonRequest = LlmJsonRequest(
            self.model,
            "Inspect the image for credentials or personal data. Never transcribe content; "
            "answer only the JSON schema with a boolean and rule classes.",
            "Classify the attached evidence image.",
            _JSON_SCHEMA,
            256,
            (LlmImagePart(content_type, data),),
        )
        for attempt in range(3):
            try:
                raw: str = await self.client.complete_json(request)
                response: _ImageResponse = _RESPONSE_ADAPTER.validate_json(raw)
                return ImageVerdict(response.contains_sensitive, response.rule_classes)
            except (ValidationError, ValueError):
                if attempt == 2:
                    raise TelescreenError(
                        ErrorCode.EVIDENCE_REJECTED, "scanner_invalid_output"
                    ) from None
            except Exception:
                if attempt == 2:
                    raise TelescreenError(ErrorCode.EVIDENCE_REJECTED, "scanner_failed") from None
        raise TelescreenError(ErrorCode.EVIDENCE_REJECTED, "scanner_failed")
