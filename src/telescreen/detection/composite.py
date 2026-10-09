"""Composition of independent detector stages."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DetectorKind, DetectorMode, ErrorCode, RuleClass
from telescreen.detection.types import (
    DetectionResult,
    Detector,
    RawFinding,
    ScanItem,
    Span,
    check_results,
)


@dataclass(frozen=True)
class CompositeDetector:
    """Combine detector stages with union or candidate-confirmation cascade."""

    stages: tuple[Detector, ...]
    mode: DetectorMode
    kind: DetectorKind = field(init=False)

    def __post_init__(self) -> None:
        """Infer the public kind from the first configured stage."""
        if not self.stages:
            raise TelescreenError(
                ErrorCode.DETECTOR_UNAVAILABLE, "composite detector has no stages"
            )
        object.__setattr__(self, "kind", self.stages[0].kind)

    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]:
        """Run configured stages and safely degrade on later-stage failure."""
        if self.mode is DetectorMode.UNION:
            batches: list[list[DetectionResult]] = await asyncio.gather(
                *(stage.detect_batch(items) for stage in self.stages)
            )
            for batch in batches:
                check_results(items, batch)
            return [
                self._merge(
                    item.item_id,
                    [finding for batch in batches for finding in batch[index].findings],
                )
                for index, item in enumerate(items)
            ]

        previous: list[DetectionResult] = await self.stages[0].detect_batch(items)
        check_results(items, previous)
        for stage in self.stages[1:]:
            candidates: list[tuple[int, int, RawFinding]] = []
            windows: list[ScanItem] = []
            for item_index, (item, result) in enumerate(zip(items, previous, strict=True)):
                for finding in result.findings:
                    left: int = max(0, finding.span.start - 300)
                    right: int = min(len(item.text), finding.span.end + 300)
                    window_id: str = f"{item.item_id}#w{len(windows)}"
                    windows.append(
                        ScanItem(
                            window_id,
                            item.source,
                            item.locator,
                            item.text[left:right],
                            item.content_type,
                        )
                    )
                    candidates.append((item_index, left, finding))
            try:
                confirmations: list[DetectionResult] = await stage.detect_batch(windows)
                check_results(windows, confirmations)
            except Exception:
                previous = [
                    DetectionResult(result.item_id, result.findings, True) for result in previous
                ]
                continue
            kept: list[list[RawFinding]] = [[] for _ in items]
            for (item_index, offset, finding), confirmation in zip(
                candidates, confirmations, strict=True
            ):
                exact: bool = finding.confidence >= 0.9 and finding.rule_class in (
                    RuleClass.CREDENTIAL,
                    RuleClass.TOKEN,
                    RuleClass.PRIVATE_KEY,
                    RuleClass.PAYMENT_CARD,
                    RuleClass.NATIONAL_ID,
                )
                confirmed: bool = any(
                    self._overlap(
                        finding.span,
                        Span(hit.span.start + offset, hit.span.end + offset),
                    )
                    for hit in confirmation.findings
                )
                if exact or confirmed:
                    kept[item_index].append(finding)
            previous = [
                DetectionResult(item.item_id, tuple(kept[index]))
                for index, item in enumerate(items)
            ]
        return previous

    @staticmethod
    def _merge(item_id: str, findings: list[RawFinding]) -> DetectionResult:
        """Merge overlapping spans, combining same-class findings."""
        ordered: list[RawFinding] = sorted(
            findings, key=lambda finding: (-finding.confidence, finding.span.start)
        )
        merged: list[RawFinding] = []
        for finding in ordered:
            collision: RawFinding | None = next(
                (prior for prior in merged if CompositeDetector._overlap(prior.span, finding.span)),
                None,
            )
            if collision is None:
                merged.append(finding)
            elif collision.rule_class == finding.rule_class:
                merged.remove(collision)
                stronger: RawFinding = (
                    collision if collision.confidence >= finding.confidence else finding
                )
                merged.append(
                    RawFinding(
                        stronger.rule_id,
                        stronger.rule_class,
                        Span(
                            min(collision.span.start, finding.span.start),
                            max(collision.span.end, finding.span.end),
                        ),
                        max(collision.confidence, finding.confidence),
                        stronger.detector,
                    )
                )
        merged.sort(key=lambda finding: finding.span.start)
        return DetectionResult(item_id, tuple(merged))

    @staticmethod
    def _overlap(left: Span, right: Span) -> bool:
        """Return whether two spans intersect."""
        return left.start < right.end and right.start < left.end
