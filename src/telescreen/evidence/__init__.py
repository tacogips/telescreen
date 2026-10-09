"""Evidence scan, store and query components."""

from telescreen.evidence.image import ImageEvidenceScanner, ImageVerdict, LlmImageEvidenceScanner
from telescreen.evidence.query import EvidenceQueryImpl
from telescreen.evidence.service import EvidenceService
from telescreen.evidence.store import EvidenceStore, LocalFileEvidenceStore
from telescreen.evidence.strip import strip_metadata
from telescreen.evidence.types import EvidenceKind, classify

__all__: list[str] = [
    "EvidenceKind",
    "EvidenceQueryImpl",
    "EvidenceService",
    "EvidenceStore",
    "ImageEvidenceScanner",
    "ImageVerdict",
    "LlmImageEvidenceScanner",
    "LocalFileEvidenceStore",
    "classify",
    "strip_metadata",
]
