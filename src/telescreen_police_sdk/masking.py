"""Safe value masking helpers for standalone Police implementations."""

from __future__ import annotations

import hashlib
import hmac


def mask_value(value: str, keep_prefix: int = 0) -> str:
    """Keep a small prefix and mask the remainder, capped at sixteen stars."""
    prefix_length: int = min(max(keep_prefix, 0), len(value))
    masked_length: int = len(value) - prefix_length
    return f"{value[:prefix_length]}{'*' * min(masked_length, 16)}[{len(value)}]"


def external_fingerprint(value: str, key: bytes) -> str:
    """Return a keyed SHA-256 fingerprint suitable for external deduplication."""
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()
