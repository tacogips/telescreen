"""Detector interfaces and rule-based implementations."""

from telescreen.detection.composite import CompositeDetector
from telescreen.detection.rule_detector import RuleDetector
from telescreen.detection.rules.catalog import PUBLIC_PREFIXES
from telescreen.detection.types import DetectionResult, Detector, RawFinding, ScanItem, Span

__all__: list[str] = [
    "CompositeDetector",
    "DetectionResult",
    "Detector",
    "PUBLIC_PREFIXES",
    "RawFinding",
    "RuleDetector",
    "ScanItem",
    "Span",
]
