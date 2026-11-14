"""Outbound message inspection, masking, and sealed delivery values."""

from dataclasses import dataclass, field

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DlpPolicy, DlpVerdict, ErrorCode
from telescreen.db.models.notifications import NotificationRow
from telescreen.detection.types import DetectionResult, Detector, ScanItem, check_results
from telescreen.masking.mask import mask_value
from telescreen.masking.redact import Redactor


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

    async def inspect(self, message: OutboundMessage) -> CensoredMessage:
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
        hit_count: int = len(subject_result.findings) + len(body_result.findings)
        if hit_count == 0:
            return CensoredMessage(message.subject, message.body, DlpVerdict.PASS, 0, _SEAL)
        if self.policy is DlpPolicy.BLOCK:
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
