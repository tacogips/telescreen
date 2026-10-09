"""Rule-based guard that rejects search fields containing sensitive values."""

from collections.abc import Sequence
from dataclasses import dataclass

from telescreen.detection import RuleDetector
from telescreen.detection.types import RawFinding


@dataclass(frozen=True)
class GuardVerdict:
    """Safe summary of an index guard decision."""

    accepted: bool
    field_names: tuple[str, ...]
    rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class IndexGuard:
    """Inspect every document field and reject the document on any hit."""

    detector: RuleDetector

    def check(self, fields: Sequence[tuple[str, str]]) -> GuardVerdict:
        """Return field names and rule ids only, never matched values."""
        names: list[str] = []
        rule_ids: list[str] = []
        for name, value in fields:
            findings: list[RawFinding] = self.detector.scan_text(value)
            for finding in findings:
                if name not in names:
                    names.append(name)
                if finding.rule_id not in rule_ids:
                    rule_ids.append(finding.rule_id)
        return GuardVerdict(not names, tuple(names), tuple(rule_ids))
