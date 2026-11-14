"""Canonical UUID identifiers must not produce partial-match PII findings."""

import pytest

from telescreen.detection import RuleDetector
from telescreen.detection.types import RawFinding

DETECTOR: RuleDetector = RuleDetector()


@pytest.mark.parametrize(
    "identifier",
    [
        "274fe70f-e651-4d33-a1ee-0210200056f2",
        "b88212d9-0963-4690-8884-f0ff5fb53bab",
        "e1d870f9-8588-4a0c-abbf-775998745197",
        "96d07688-6347-4863-ad01-53e9d2a45044",
    ],
)
def test_uuid_digit_runs_are_not_phone_or_my_number(identifier: str) -> None:
    """These UUIDs previously matched phone_jp or my_number inside their digit groups."""
    assert DETECTOR.scan_text(f"target {identifier} updated") == []


def test_real_values_next_to_uuid_are_still_found() -> None:
    """A phone number beside an identifier is still detected."""
    text: str = "issue 274fe70f-e651-4d33-a1ee-0210200056f2 phone 03-1234-5678"
    findings: list[RawFinding] = DETECTOR.scan_text(text)
    assert [
        (finding.rule_id, text[finding.span.start : finding.span.end]) for finding in findings
    ] == [("phone_jp", "03-1234-5678")]


def test_digit_run_inside_longer_hex_token_is_not_treated_as_uuid() -> None:
    """Only canonical 8-4-4-4-12 identifiers are excluded."""
    text: str = "tel 0312345678"
    findings: list[RawFinding] = DETECTOR.scan_text(text)
    assert [finding.rule_id for finding in findings] == ["phone_jp"]
