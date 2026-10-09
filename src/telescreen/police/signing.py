"""Signing helpers for the independent Bureau side of Police push transport."""

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class SignatureHeaders:
    """The three signed request headers, with nonce hidden from repr."""

    timestamp: str
    nonce: str
    signature: str


def derive_push_secret(master: bytes, worker_id: str) -> str:
    """Derive the stable base64url secret for one registered push worker."""
    digest: bytes = hmac.new(
        master, b"police-push\0" + worker_id.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def sign(secret: str, body: bytes, timestamp: str, nonce: str) -> str:
    """Sign exact request bytes using the UTF-8 signing secret."""
    message: bytes = timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body
    digest: bytes = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return "v1=" + digest.hex()


def verify_signature(
    headers: SignatureHeaders,
    body: bytes,
    secret: str,
    now: datetime,
    nonce_store_result: bool,
) -> bool:
    """Verify time window, nonce shape, replay-store result, and signature."""
    if re.fullmatch(r"[0-9a-fA-F]{32}", headers.nonce) is None:
        return False
    try:
        timestamp_value: int = int(headers.timestamp)
        current_value: int = int(now.timestamp())
    except ValueError:
        return False
    if abs(current_value - timestamp_value) > 300 or not nonce_store_result:
        return False
    expected: str = sign(secret, body, headers.timestamp, headers.nonce)
    return hmac.compare_digest(expected, headers.signature)


def replay_expiry(now: datetime) -> datetime:
    """Return the end of the nonce replay retention window."""
    return now + timedelta(seconds=600)
