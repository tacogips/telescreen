"""Seal detector spans into safe, persistable masked findings."""

from dataclasses import dataclass

from telescreen.core.vocabulary import severity_for
from telescreen.detection.types import DetectionResult, RawFinding, ScanItem
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.mask import mask_value
from telescreen.masking.redact import Redactor
from telescreen.ports.findings import MaskedFinding


@dataclass(frozen=True)
class Masker:
    """Convert transient detector output into masked findings."""

    fingerprinter: Fingerprinter
    redactor: Redactor

    def seal(self, item: ScanItem, result: DetectionResult) -> list[MaskedFinding]:
        """Build findings containing no raw value or raw locator."""
        sealed: list[MaskedFinding] = []
        for finding in result.findings:
            value: str = item.text[finding.span.start : finding.span.end]
            snippet: str = self._snippet(item.text, finding, result)
            safe_snippet: str = self.redactor.redact(snippet).text
            if self.redactor.has_hits(safe_snippet):
                safe_snippet = "[REDACTED]"
            sealed.append(
                MaskedFinding(
                    rule_id=finding.rule_id,
                    rule_class=finding.rule_class,
                    severity=severity_for(finding.rule_class),
                    detector=finding.detector,
                    confidence=finding.confidence,
                    masked_snippet=safe_snippet[:200],
                    location=self.display_locator(item.locator),
                    fingerprint=self.fingerprinter.fingerprint(
                        finding.rule_id, value, finding.rule_class
                    ),
                    fingerprint_key_id=self.fingerprinter.key_id,
                )
            )
        return sealed

    def display_locator(self, raw_locator: str) -> str:
        """Return a redacted display-safe resource locator."""
        return self.redactor.redact(raw_locator).text

    def _snippet(self, text: str, target: RawFinding, result: DetectionResult) -> str:
        """Build a line-bounded context window with every finding masked."""
        line_start: int = text.rfind("\n", 0, target.span.start) + 1
        line_end: int = text.find("\n", target.span.end)
        if line_end < 0:
            line_end = len(text)
        start: int = max(line_start, target.span.start - 40)
        end: int = min(line_end, target.span.end + 40)
        snippet: str = text[start:end]
        relevant: list[RawFinding] = [
            finding
            for finding in result.findings
            if finding.span.start < end and finding.span.end > start
        ]
        for finding in reversed(relevant):
            raw: str = text[finding.span.start : finding.span.end]
            replacement: str = mask_value(raw, finding.rule_id, finding.rule_class)
            local_start: int = max(finding.span.start, start) - start
            local_end: int = min(finding.span.end, end) - start
            snippet = snippet[:local_start] + replacement + snippet[local_end:]
        return snippet[:200]
