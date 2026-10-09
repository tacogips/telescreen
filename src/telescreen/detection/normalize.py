"""Unicode normalization with offsets mapped to source text."""

import unicodedata
from dataclasses import dataclass, field

from telescreen.detection.types import Span


@dataclass(frozen=True)
class NormalizedText:
    """Normalized text and normalized-index to source-index mapping."""

    text: str = field(repr=False)
    index_map: tuple[int, ...]


def normalize(text: str) -> NormalizedText:
    """Apply per-character NFKC while retaining source offsets."""
    parts: list[str] = []
    indexes: list[int] = []
    for index, character in enumerate(text):
        expanded: str = unicodedata.normalize("NFKC", character)
        parts.append(expanded)
        indexes.extend(index for _ in expanded)
    indexes.append(len(text))
    return NormalizedText("".join(parts), tuple(indexes))


def to_original(span_in_normalized: Span, normalized: NormalizedText) -> Span:
    """Translate a normalized span to the smallest covering source span."""
    start: int = normalized.index_map[span_in_normalized.start]
    end_index: int = span_in_normalized.end
    previous_source: int = normalized.index_map[end_index - 1]
    next_source: int = normalized.index_map[end_index]
    end: int = previous_source + 1 if next_source == previous_source else next_source
    return Span(start, end)
