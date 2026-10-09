"""Rule-based redaction for free text and output boundaries."""

from dataclasses import dataclass

from telescreen.core.vocabulary import RuleClass
from telescreen.detection import RuleDetector
from telescreen.detection.types import RawFinding
from telescreen.masking.mask import mask_value


@dataclass(frozen=True)
class RedactionResult:
    """Redacted text and non-sensitive summary of detected classes."""

    text: str
    hit_count: int
    rule_classes: tuple[RuleClass, ...]


@dataclass(frozen=True)
class Redactor:
    """Mask all rule detector spans in a text value."""

    detector: RuleDetector

    def redact(self, text: str) -> RedactionResult:
        """Mask every detected span and report only safe metadata."""
        findings: list[RawFinding] = self.detector.scan_text(text)
        masked: str = text
        for finding in reversed(findings):
            raw: str = text[finding.span.start : finding.span.end]
            replacement: str = mask_value(raw, finding.rule_id, finding.rule_class)
            masked = masked[: finding.span.start] + replacement + masked[finding.span.end :]
        classes: list[RuleClass] = []
        for finding in findings:
            if finding.rule_class not in classes:
                classes.append(finding.rule_class)
        return RedactionResult(masked, len(findings), tuple(classes))

    def has_hits(self, text: str) -> bool:
        """Return whether rule-based detection finds sensitive content."""
        return bool(self.detector.scan_text(text))

    @staticmethod
    def default() -> "Redactor":
        """Create a reusable redactor with the built-in rules."""
        return Redactor(RuleDetector())
