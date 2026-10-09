"""Optional token-classification detector with lazy transformers loading."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Protocol

from pydantic import TypeAdapter

from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.vocabulary import DetectorKind, ErrorCode, RuleClass
from telescreen.detection.types import DetectionResult, RawFinding, ScanItem, Span


@dataclass(frozen=True)
class EntitySpan:
    """A token classifier result with offsets into the original text."""

    start: int
    end: int
    label: str
    score: float


class TokenClassifier(Protocol):
    """Interface for optional token-classification implementations."""

    def classify(self, texts: Sequence[str]) -> list[list[EntitySpan]]:
        """Classify a batch of text inputs."""


class _PipelineCallable(Protocol):
    """Callable interface for the optional token classification pipeline."""

    def __call__(self, texts: list[str]) -> list[list[_PipelineEntity]]:
        """Return token-classification entities for a batch."""


class _PipelineFactory(Protocol):
    """Typed loading seam for the optional transformers package."""

    def __call__(self, task: str, *, model: str, aggregation_strategy: str) -> _PipelineCallable:
        """Create a token classification pipeline."""


@dataclass(frozen=True)
class MlDetector:
    """Run a token classifier off-loop and map supported labels to findings."""

    classifier: TokenClassifier
    label_map: tuple[tuple[str, RuleClass], ...] = (
        ("EMAIL", RuleClass.EMAIL),
        ("PHONE", RuleClass.PHONE),
        ("ADDRESS", RuleClass.ADDRESS),
        ("CREDENTIAL", RuleClass.CREDENTIAL),
    )
    kind: DetectorKind = DetectorKind.ML

    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]:
        """Classify each input in a worker thread and validate the span contract."""
        spans: list[list[EntitySpan]] = await asyncio.to_thread(
            self.classifier.classify, [item.text for item in items]
        )
        if len(spans) != len(items):
            raise TelescreenError(
                ErrorCode.DETECTOR_UNAVAILABLE, "ML classifier returned an invalid batch"
            )
        results: list[DetectionResult] = []
        for item, entities in zip(items, spans, strict=True):
            findings: list[RawFinding] = []
            for entity in entities:
                rule_class: RuleClass | None = next(
                    (value for label, value in self.label_map if label == entity.label.upper()),
                    None,
                )
                if rule_class is None or entity.start < 0 or entity.end <= entity.start:
                    continue
                if entity.end > len(item.text):
                    continue
                findings.append(
                    RawFinding(
                        f"ml_{rule_class.value}",
                        rule_class,
                        Span(entity.start, entity.end),
                        entity.score,
                        self.kind,
                    )
                )
            results.append(DetectionResult(item.item_id, tuple(findings)))
        return results


@dataclass(frozen=True)
class _PipelineEntity:
    """Validated aggregated transformer pipeline entity."""

    start: int
    end: int
    entity_group: str
    score: float


_PIPELINE_ADAPTER: TypeAdapter[list[list[_PipelineEntity]]] = TypeAdapter(
    list[list[_PipelineEntity]]
)


@dataclass(frozen=True)
class _TransformersClassifier:
    """Adapt the optional transformers pipeline to the typed classifier protocol."""

    pipeline: _PipelineCallable

    def classify(self, texts: Sequence[str]) -> list[list[EntitySpan]]:
        """Convert pipeline output through a validated JSON boundary."""
        parsed: list[list[_PipelineEntity]] = _PIPELINE_ADAPTER.validate_python(
            self.pipeline(list(texts))
        )
        return [
            [EntitySpan(span.start, span.end, span.entity_group, span.score) for span in row]
            for row in parsed
        ]


def load_transformers_classifier(model: str) -> TokenClassifier:
    """Load transformers only when an ML detector is explicitly selected."""
    try:
        module: ModuleType = importlib.import_module("transformers")
        pipeline_factory: _PipelineFactory = module.pipeline
    except (ImportError, AttributeError):
        raise ConfigError("ml detector requires the 'ml' extra") from None
    try:
        pipeline: _PipelineCallable = pipeline_factory(
            "token-classification", model=model, aggregation_strategy="simple"
        )
    except Exception:
        raise ConfigError("ml detector could not load the configured model") from None
    return _TransformersClassifier(pipeline)
