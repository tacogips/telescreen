"""Webhook signing uses UTF-8 bytes of the registration signing_secret string.

The secret is never base64-decoded; this module also provides replay protection for
Police push mode.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from enum import StrEnum


class VerifyResult(StrEnum):
    """Push signature verification outcome."""

    OK = "ok"
    BAD_SIGNATURE = "bad_signature"
    STALE = "stale"
    REPLAY = "replay"
    MISSING = "missing"


@dataclass
class NonceCache:
    """Small in-memory nonce cache with a bounded TTL."""

    ttl_seconds: int = 600
    _entries: tuple[tuple[str, int], ...] = field(default=(), repr=False)

    def seen_or_add(self, nonce: str, now: int) -> bool:
        """Return true for an already-seen nonce and otherwise remember it."""
        active: tuple[tuple[str, int], ...] = tuple(
            (key, expires) for key, expires in self._entries if expires > now
        )
        if any(key == nonce for key, _ in active):
            self._entries = active
            return True
        self._entries = active + ((nonce, now + self.ttl_seconds),)
        return False


def sign(secret: bytes, body: bytes, timestamp: int, nonce: str) -> str:
    """Return the versioned HMAC signature for exact request bytes."""
    message: bytes = f"{timestamp}.{nonce}.".encode() + body
    digest: str = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def signature_headers(
    secret: bytes, body: bytes, now: int, nonce: str
) -> tuple[tuple[str, str], ...]:
    """Build the timestamp, nonce and signature headers."""
    return (
        ("X-Telescreen-Timestamp", str(now)),
        ("X-Telescreen-Nonce", nonce),
        ("X-Telescreen-Signature", sign(secret, body, now, nonce)),
    )


def verify(
    headers: tuple[tuple[str, str], ...] | list[tuple[str, str]],
    body: bytes,
    secret: bytes,
    now: int,
    seen: NonceCache,
) -> VerifyResult:
    """Verify required headers, clock window, signature and nonce uniqueness."""
    normalized: tuple[tuple[str, str], ...] = tuple(
        (name.lower(), value) for name, value in headers
    )
    timestamp_text: str | None = next(
        (value for name, value in normalized if name == "x-telescreen-timestamp"), None
    )
    nonce: str | None = next(
        (value for name, value in normalized if name == "x-telescreen-nonce"), None
    )
    signature: str | None = next(
        (value for name, value in normalized if name == "x-telescreen-signature"), None
    )
    if timestamp_text is None or nonce is None or signature is None:
        return VerifyResult.MISSING
    try:
        timestamp: int = int(timestamp_text)
    except ValueError:
        return VerifyResult.MISSING
    if abs(now - timestamp) > 300:
        return VerifyResult.STALE
    expected: str = sign(secret, body, timestamp, nonce)
    if not hmac.compare_digest(signature, expected):
        return VerifyResult.BAD_SIGNATURE
    if seen.seen_or_add(nonce, now):
        return VerifyResult.REPLAY
    return VerifyResult.OK
