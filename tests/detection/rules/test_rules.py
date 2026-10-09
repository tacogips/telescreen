"""Behavioral tests for the deterministic detector and its composition."""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DetectorKind, DetectorMode, ErrorCode, RuleClass
from telescreen.detection import (
    CompositeDetector,
    DetectionResult,
    RawFinding,
    RuleDetector,
    ScanItem,
    Span,
)
from telescreen.detection.normalize import NormalizedText, normalize, to_original
from telescreen.detection.rules.validators import (
    is_placeholder,
    jwt_header_valid,
    known_card_iin,
    luhn_valid,
    my_number_valid,
    shannon_entropy,
)
from telescreen.detection.types import check_results
from tests.support.samples import Sample, all_samples, aws_secret_access_key, my_number


@dataclass(frozen=True)
class FakeDetector:
    """A deterministic detector stage for composition tests."""

    kind: DetectorKind
    findings: tuple[RawFinding, ...]
    fail: bool = False

    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]:
        """Return configured spans for all requested items."""
        if self.fail:
            raise RuntimeError("safe failure")
        return [DetectionResult(item.item_id, self.findings) for item in items]


@dataclass(frozen=True)
class MarkerConfirmer:
    """Confirm only cascade windows containing a configured marker."""

    kind: DetectorKind
    marker: str

    async def detect_batch(self, items: Sequence[ScanItem]) -> list[DetectionResult]:
        """Return marker spans in each window's local coordinate space."""
        results: list[DetectionResult] = []
        for item in items:
            start: int = item.text.find(self.marker)
            findings: tuple[RawFinding, ...] = ()
            if start >= 0:
                finding: RawFinding = RawFinding(
                    "marker_confirmation",
                    RuleClass.ADDRESS,
                    Span(start, start + len(self.marker)),
                    0.8,
                    self.kind,
                )
                findings = (finding,)
            results.append(DetectionResult(item.item_id, findings))
        return results


def _item(text: str, item_id: str = "one") -> ScanItem:
    """Build a transient test item."""
    return ScanItem(item_id, "test", "record", text)


def test_all_samples_detect_expected_class_and_exact_span() -> None:
    """Every shared sample is recognized without expanding its span."""
    detector: RuleDetector = RuleDetector()
    for sample in all_samples():
        text: str = f"{sample.name}: {sample.value}"
        findings: list[RawFinding] = detector.scan_text(text)
        matching: list[RawFinding] = [
            finding for finding in findings if finding.rule_class is sample.rule_class
        ]
        assert len(matching) == 1, sample.name
        finding: RawFinding = matching[0]
        expected_value: str = (
            sample.value.partition("=")[2] if sample.name == "password_assignment" else sample.value
        )
        if text[finding.span.start : finding.span.end] != expected_value:
            raise AssertionError(sample.name)


def test_my_number_boundaries_allow_adjacent_separated_digits() -> None:
    """My Number matches remain valid beside ages, years, counts, and row numbers."""
    detector: RuleDetector = RuleDetector()
    cases: tuple[tuple[str, Span], ...] = (
        ("山田太郎 " + "1234" + " " + "5678" + " " + "9018" + " 35歳", Span(5, 19)),
        ("1234" + "-" + "5678" + "-" + "9018" + " 2025", Span(0, 14)),
        ("ID 5 " + "1234" + " " + "5678" + " " + "9018", Span(5, 19)),
        ("123456" + "789018" + " 1件", Span(0, 12)),
    )
    for text, expected_span in cases:
        findings: list[RawFinding] = detector.scan_text(text)
        assert len(findings) == 1
        assert findings[0].rule_id == "my_number"
        assert findings[0].span == expected_span


def test_rule_scan_is_linear_on_long_runs() -> None:
    """Long whitespace-free content does not trigger quadratic email matching."""
    detector: RuleDetector = RuleDetector()
    text: str = "a." * 100_000
    started_at: float = time.perf_counter()
    detector.scan_text(text)
    elapsed: float = time.perf_counter() - started_at
    assert elapsed < 5.0


def test_normalization_preserves_expansion_source_span() -> None:
    """NFKC expansion maps each normalized character back to its source."""
    normalized: NormalizedText = normalize("x㍻y")
    start: int = normalized.text.index("平成")
    span: Span = to_original(Span(start, start + 2), normalized)
    assert normalized.text == "x平成y"
    assert span == Span(1, 2)


def test_full_width_phone_and_my_number_keep_original_offsets() -> None:
    """Full-width digits normalize for matching and return source spans."""
    detector: RuleDetector = RuleDetector()
    phone: str = "０９０－１２３４－５６７８"
    phone_hits: list[RawFinding] = detector.scan_text(phone)
    assert any(
        phone_hit.rule_id == "phone_jp"
        and phone[phone_hit.span.start : phone_hit.span.end] == phone
        for phone_hit in phone_hits
    )
    number: str = my_number().value.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
    number_hits: list[RawFinding] = detector.scan_text(number)
    assert any(
        number_hit.rule_id == "my_number"
        and number[number_hit.span.start : number_hit.span.end] == number
        for number_hit in number_hits
    )


def test_invalid_checks_placeholders_and_context_are_rejected() -> None:
    """Checksums, placeholders, JWT headers, and AWS context gate findings."""
    detector: RuleDetector = RuleDetector()
    assert not any(hit.rule_id == "credit_card" for hit in detector.scan_text("4111111111111112"))
    assert not any(hit.rule_id == "my_number" for hit in detector.scan_text("123456789010"))
    aws_sample: Sample = aws_secret_access_key()
    assert len(aws_sample.value) == 40
    assert not any(
        hit.rule_id == "aws_secret_access_key" for hit in detector.scan_text(aws_sample.value)
    )
    aws_context: str = "aws_secret_access_key: "
    aws_positive: list[RawFinding] = detector.scan_text(aws_context + aws_sample.value)
    aws_positive_matches: list[RawFinding] = [
        hit for hit in aws_positive if hit.rule_id == "aws_secret_access_key"
    ]
    assert len(aws_positive_matches) == 1
    assert aws_positive_matches[0].span == Span(len(aws_context), len(aws_context) + 40)
    for placeholder in ("changeme", "${DB_PASS}", "<secret>"):
        assert not any(
            hit.rule_id == "password_assignment"
            for hit in detector.scan_text(f"password={placeholder}")
        )
    assert not any(hit.rule_id == "jwt" for hit in detector.scan_text("eyJ.invalid.signature"))


def test_validator_edges_and_private_key_span() -> None:
    """Validator helpers and PEM spans follow the rule contract."""
    card: str = "4" + "1" * 15
    assert luhn_valid(card)
    assert known_card_iin(card)
    assert my_number_valid(my_number().value)
    assert shannon_entropy("abcdefghij0123456789") > 4.0
    assert jwt_header_valid("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature")
    assert is_placeholder("${DB_PASS}")
    pem: str = (
        "before\n-----BEGIN RSA PRIVATE KEY-----\nexample\n-----END RSA PRIVATE KEY-----\nafter"
    )
    finding: RawFinding = next(
        hit for hit in RuleDetector().scan_text(pem) if hit.rule_id == "private_key"
    )
    assert (
        pem[finding.span.start : finding.span.end]
        == "-----BEGIN RSA PRIVATE KEY-----\nexample\n-----END RSA PRIVATE KEY-----"
    )


def test_address_variants_and_card_phone_overlap() -> None:
    """Japanese address forms match and card priority suppresses phone overlap."""
    detector: RuleDetector = RuleDetector()
    address: list[RawFinding] = detector.scan_text("住所: 東京都千代田区丸の内1丁目1-1")
    assert any(hit.rule_id == "address_jp" for hit in address)
    postal: list[RawFinding] = detector.scan_text("〒100-0005 東京都")
    assert any(hit.rule_id == "address_jp" for hit in postal)
    overlaps: list[RawFinding] = detector.scan_text("4111111111111111")
    assert sum(hit.rule_id == "credit_card" for hit in overlaps) == 1
    assert not any(hit.rule_id == "phone_jp" for hit in overlaps)

    hyphenated_card: list[RawFinding] = detector.scan_text("4111-0312-3456-7892")
    assert len(hyphenated_card) == 1
    assert hyphenated_card[0].rule_id == "credit_card"
    assert hyphenated_card[0].span == Span(0, 19)
    assert not any(hit.rule_id == "phone_jp" for hit in hyphenated_card)

    grouped_card: list[RawFinding] = detector.scan_text("4111 0000 0008 0000")
    assert len(grouped_card) == 1
    assert grouped_card[0].rule_id == "credit_card"
    assert grouped_card[0].span == Span(0, 19)
    assert not any(hit.rule_id == "my_number" for hit in grouped_card)


def test_check_results_rejects_wrong_count_and_spans() -> None:
    """Contract validation raises a stable detector error."""
    item: ScanItem = _item("safe")
    with pytest.raises(TelescreenError) as caught:
        check_results((item,), ())
    assert caught.value.code is ErrorCode.DETECTOR_UNAVAILABLE
    bad: DetectionResult = DetectionResult(
        "one", (RawFinding("x", RuleClass.CREDENTIAL, Span(-1, 2), 1.0, DetectorKind.RULE),)
    )
    with pytest.raises(TelescreenError):
        check_results((item,), (bad,))


def test_union_merges_overlapping_same_class_findings() -> None:
    """Union mode combines same-class overlapping ranges."""
    first: RawFinding = RawFinding("a", RuleClass.CREDENTIAL, Span(1, 4), 0.8, DetectorKind.RULE)
    second: RawFinding = RawFinding("b", RuleClass.CREDENTIAL, Span(3, 6), 0.9, DetectorKind.ML)
    composite: CompositeDetector = CompositeDetector(
        (FakeDetector(DetectorKind.RULE, (first,)), FakeDetector(DetectorKind.ML, (second,))),
        DetectorMode.UNION,
    )
    results: list[DetectionResult] = asyncio.run(composite.detect_batch((_item("abcdefgh"),)))
    assert len(results[0].findings) == 1
    assert results[0].findings[0].span == Span(1, 6)
    assert results[0].findings[0].confidence == 0.9


def test_cascade_keeps_exact_and_confirmed_candidates_and_degrades() -> None:
    """Cascade scopes candidates, retains exact candidates, and marks failed stage."""
    exact: RawFinding = RawFinding(
        "exact", RuleClass.CREDENTIAL, Span(1, 4), 0.99, DetectorKind.RULE
    )
    medium_a: RawFinding = RawFinding(
        "medium_a", RuleClass.ADDRESS, Span(700, 705), 0.6, DetectorKind.RULE
    )
    medium_b: RawFinding = RawFinding(
        "medium_b", RuleClass.ADDRESS, Span(1200, 1205), 0.6, DetectorKind.RULE
    )
    first_stage: FakeDetector = FakeDetector(DetectorKind.RULE, (exact, medium_a, medium_b))
    text: str = "xKEY" + ("z" * 696) + "ALPHA" + ("z" * 495) + "BRAVO" + ("z" * 295)
    assert len(text) == 1500
    second_stage: MarkerConfirmer = MarkerConfirmer(DetectorKind.ML, "ALPHA")
    composite: CompositeDetector = CompositeDetector(
        (first_stage, second_stage), DetectorMode.CASCADE
    )
    results: list[DetectionResult] = asyncio.run(composite.detect_batch((_item(text),)))
    assert {finding.rule_id for finding in results[0].findings} == {"exact", "medium_a"}
    assert next(
        finding for finding in results[0].findings if finding.rule_id == "exact"
    ).span == Span(1, 4)
    assert next(
        finding for finding in results[0].findings if finding.rule_id == "medium_a"
    ).span == Span(700, 705)
    assert not any(finding.rule_id == "medium_b" for finding in results[0].findings)
    failing: CompositeDetector = CompositeDetector(
        (first_stage, FakeDetector(DetectorKind.ML, (), True)), DetectorMode.CASCADE
    )
    degraded: list[DetectionResult] = asyncio.run(failing.detect_batch((_item(text),)))
    assert degraded[0].degraded
    assert degraded[0].findings == (exact, medium_a, medium_b)


def test_rule_catalog_has_all_required_ids() -> None:
    """Catalog exposes all fifteen stable rule identifiers."""
    expected: set[str] = {
        "aws_access_key_id",
        "aws_secret_access_key",
        "github_token",
        "slack_token",
        "google_api_key",
        "private_key",
        "jwt",
        "password_assignment",
        "credit_card",
        "my_number",
        "email",
        "phone_jp",
        "phone_intl",
        "address_jp",
        "address_en",
    }
    assert {rule.rule_id for rule in RuleDetector().rules} == expected
