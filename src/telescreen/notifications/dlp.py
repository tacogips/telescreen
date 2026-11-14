"""Outbound message inspection, masking, and sealed delivery values."""

from dataclasses import dataclass, field

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DlpPolicy, DlpVerdict, ErrorCode
from telescreen.db.models.notifications import NotificationRow
from telescreen.detection.types import DetectionResult, Detector, ScanItem, check_results
from telescreen.masking.mask import mask_value
from telescreen.masking.redact import RedactionResult, Redactor


@dataclass(frozen=True)
class OutboundMessage:
    """Message content that must pass the outbound DLP gate."""

    subject: str = field(repr=False)
    body: str = field(repr=False)


@dataclass(frozen=True)
class _Seal:
    """Unforgeable package-local construction marker."""


_SEAL: _Seal = _Seal()


@dataclass(frozen=True)
class CensoredMessage:
    """Message safe for persistence and external delivery."""

    subject: str = field(repr=False)
    body: str = field(repr=False)
    verdict: DlpVerdict
    hit_count: int
    seal: _Seal = field(repr=False)

    def __post_init__(self) -> None:
        """Reject values not created by this module's gate or rehydrator."""
        if self.seal is not _SEAL:
            raise TypeError("CensoredMessage can only be created by the notifications package")


@dataclass(frozen=True)
class DlpGate:
    """Inspect outbound subject and body, masking or blocking detected values."""

    detector: Detector
    redactor: Redactor
    policy: DlpPolicy

    async def inspect(self, message: OutboundMessage, prior_hits: int = 0) -> CensoredMessage:
        """Return a sealed message that contains no detected raw value."""
        items: tuple[ScanItem, ScanItem] = (
            ScanItem("subject", "notification", "subject", message.subject),
            ScanItem("body", "notification", "body", message.body),
        )
        results: list[DetectionResult] = await self.detector.detect_batch(items)
        check_results(items, results)
        subject_result: DetectionResult | None = next(
            (result for result in results if result.item_id == "subject"), None
        )
        body_result: DetectionResult | None = next(
            (result for result in results if result.item_id == "body"), None
        )
        if subject_result is None or body_result is None:
            raise TelescreenError(
                ErrorCode.DETECTOR_UNAVAILABLE, "detector returned an invalid batch"
            )
        detected_hits: int = len(subject_result.findings) + len(body_result.findings)
        hit_count: int = detected_hits + prior_hits
        if hit_count == 0:
            return CensoredMessage(message.subject, message.body, DlpVerdict.PASS, 0, _SEAL)
        if self.policy is DlpPolicy.BLOCK and (detected_hits > 0 or prior_hits > 0):
            return CensoredMessage(
                "[BLOCKED BY DLP]", "[BLOCKED BY DLP]", DlpVerdict.BLOCKED, hit_count, _SEAL
            )

        subject: str = _mask_spans(message.subject, subject_result)
        body: str = _mask_spans(message.body, body_result)
        if self.redactor.has_hits(subject) or self.redactor.has_hits(body):
            return CensoredMessage(
                "[BLOCKED BY DLP]", "[BLOCKED BY DLP]", DlpVerdict.BLOCKED, hit_count, _SEAL
            )
        return CensoredMessage(subject, body, DlpVerdict.MASKED, hit_count, _SEAL)

    async def inspect_redacted(
        self, message: OutboundMessage, prior_hits: int = 0
    ) -> CensoredMessage:
        """Apply the rule redactor, then inspect; redactor hits count as DLP hits.

        A redactor hit blocks under the block policy and otherwise turns a passing
        verdict into a masked one, so pre-masking never hides a detected value.
        """
        subject: RedactionResult = self.redactor.redact(message.subject)
        body: RedactionResult = self.redactor.redact(message.body)
        redacted_hits: int = subject.hit_count + body.hit_count + prior_hits
        if redacted_hits and self.policy is DlpPolicy.BLOCK:
            return CensoredMessage(
                "[BLOCKED BY DLP]", "[BLOCKED BY DLP]", DlpVerdict.BLOCKED, redacted_hits, _SEAL
            )
        inspected: CensoredMessage = await self.inspect(OutboundMessage(subject.text, body.text))
        if redacted_hits == 0:
            return inspected
        verdict: DlpVerdict = (
            DlpVerdict.BLOCKED if inspected.verdict is DlpVerdict.BLOCKED else DlpVerdict.MASKED
        )
        return CensoredMessage(
            inspected.subject,
            inspected.body,
            verdict,
            inspected.hit_count + redacted_hits,
            _SEAL,
        )


def _mask_spans(text: str, result: DetectionResult) -> str:
    """Mask detector spans from right to left so original offsets remain valid."""
    masked: str = text
    for finding in reversed(result.findings):
        value: str = text[finding.span.start : finding.span.end]
        replacement: str = mask_value(value, finding.rule_id, finding.rule_class)
        masked = masked[: finding.span.start] + replacement + masked[finding.span.end :]
    return masked


def rehydrate(row: NotificationRow) -> CensoredMessage:
    """Rebuild a sealed delivery value from the already-safe notification row."""
    return CensoredMessage(
        row.subject,
        row.body,
        DlpVerdict(row.dlp_verdict),
        row.dlp_hit_count,
        _SEAL,
    )
