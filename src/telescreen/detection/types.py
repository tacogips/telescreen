"""Shared typed contracts for sensitive-data detectors."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DetectorKind, ErrorCode, RuleClass


@dataclass(frozen=True)
class ScanItem:
    """A transient text item presented to a detector."""

    item_id: str
    source: str
    locator: str
    text: str = field(repr=False)
    content_type: str = "text/plain"


@dataclass(frozen=True)
class Span:
    """A half-open character range in the original item text."""

    start: int
    end: int


@dataclass(frozen=True)
class RawFinding:
    """A detector result containing offsets but no matched content."""

    rule_id: str
    rule_class: RuleClass
    span: Span
    confidence: float
    detector: DetectorKind


@dataclass(frozen=True)
class DetectionResult:
    """Findings for one input item."""

    item_id: str
    findings: tuple[RawFinding, ...]
    degraded: bool = False


class Detector(Protocol):
    """Batched detector interface implemented by every backend."""

    @property
    def kind(self) -> DetectorKind: ...

    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]: ...


def check_results(items: Sequence[ScanItem], results: Sequence[DetectionResult]) -> None:
    """Reject batches with missing, reordered, or out-of-range results."""
    if len(items) != len(results):
        raise TelescreenError(ErrorCode.DETECTOR_UNAVAILABLE, "detector returned an invalid batch")
    for item, result in zip(items, results, strict=True):
        if item.item_id != result.item_id:
            raise TelescreenError(
                ErrorCode.DETECTOR_UNAVAILABLE, "detector returned an invalid batch"
            )
        for finding in result.findings:
            if (
                finding.span.start < 0
                or finding.span.end <= finding.span.start
                or finding.span.end > len(item.text)
            ):
                raise TelescreenError(
                    ErrorCode.DETECTOR_UNAVAILABLE, "detector returned an invalid span"
                )
