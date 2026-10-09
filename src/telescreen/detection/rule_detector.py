"""Deterministic synchronous rules and the batched rule detector."""

from collections.abc import Sequence
from dataclasses import dataclass

from telescreen.core.vocabulary import DetectorKind, severity_for
from telescreen.detection.normalize import NormalizedText, normalize, to_original
from telescreen.detection.rules.catalog import RULES, RuleSpec
from telescreen.detection.types import DetectionResult, RawFinding, ScanItem, Span


@dataclass(frozen=True)
class RuleDetector:
    """Run configured local rules over transient text."""

    rules: tuple[RuleSpec, ...] = RULES
    kind: DetectorKind = DetectorKind.RULE

    def scan_text(self, text: str) -> list[RawFinding]:
        """Return span-only findings for a text value."""
        normalized: NormalizedText = normalize(text)
        candidates: list[RawFinding] = []
        for rule in self.rules:
            for match in rule.pattern.finditer(normalized.text):
                value: str = match.group(rule.value_group)
                if rule.validator is not None and not rule.validator(value):
                    continue
                if rule.keywords and not self._has_keyword(
                    normalized.text,
                    match.start(rule.value_group),
                    match.end(rule.value_group),
                    rule,
                ):
                    continue
                span: Span = to_original(
                    Span(match.start(rule.value_group), match.end(rule.value_group)), normalized
                )
                candidates.append(
                    RawFinding(rule.rule_id, rule.rule_class, span, rule.confidence, self.kind)
                )
        candidates.sort(key=self._priority, reverse=True)
        survivors: list[RawFinding] = []
        for candidate in candidates:
            overlaps: list[RawFinding] = [
                finding for finding in survivors if self._overlap(finding.span, candidate.span)
            ]
            if not overlaps:
                survivors.append(candidate)
            elif all(finding.rule_class == candidate.rule_class for finding in overlaps):
                survivors = [finding for finding in survivors if finding not in overlaps]
                start: int = min(
                    [candidate.span.start, *(finding.span.start for finding in overlaps)]
                )
                end: int = max([candidate.span.end, *(finding.span.end for finding in overlaps)])
                strongest: RawFinding = max([candidate, *overlaps], key=self._priority)
                survivors.append(
                    RawFinding(
                        strongest.rule_id,
                        strongest.rule_class,
                        Span(start, end),
                        strongest.confidence,
                        strongest.detector,
                    )
                )
        return sorted(survivors, key=lambda finding: finding.span.start)

    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]:
        """Detect each item without returning its raw text."""
        return [DetectionResult(item.item_id, tuple(self.scan_text(item.text))) for item in items]

    @staticmethod
    def _has_keyword(text: str, start: int, end: int, rule: RuleSpec) -> bool:
        """Check rule keywords around a candidate span."""
        window_start: int = max(0, start - rule.keyword_window)
        window_end: int = min(len(text), end + rule.keyword_window)
        context: str = text[window_start:window_end].lower()
        return any(keyword in context for keyword in rule.keywords)

    @staticmethod
    def _overlap(left: Span, right: Span) -> bool:
        """Return whether two non-empty spans intersect."""
        return left.start < right.end and right.start < left.end

    @staticmethod
    def _priority(finding: RawFinding) -> tuple[float, int, int]:
        """Sort stronger and longer findings first."""
        return (
            finding.confidence,
            int(severity_for(finding.rule_class).value == "high"),
            finding.span.end - finding.span.start,
        )
