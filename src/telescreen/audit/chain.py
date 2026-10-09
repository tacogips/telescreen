"""Canonical audit-chain hashing."""

import hashlib
from datetime import datetime

from telescreen.core.clock import ensure_utc
from telescreen.core.json import JsonValue, dump_json, json_int, json_object, json_str

GENESIS_HASH: str = "0" * 64


def compute_hash(
    prev_hash: str,
    seq: int,
    record_id: str,
    at: datetime,
    actor_kind: str,
    actor_ref: str,
    action: str,
    target_kind: str,
    target_id: str,
    summary: str,
    details_json: str,
) -> str:
    """Compute the SHA-256 hash of one canonical audit record."""
    value: JsonValue = json_object(
        (
            ("seq", json_int(seq)),
            ("id", json_str(record_id)),
            ("at", json_str(ensure_utc(at).isoformat())),
            ("actor_kind", json_str(actor_kind)),
            ("actor_ref", json_str(actor_ref)),
            ("action", json_str(action)),
            ("target_kind", json_str(target_kind)),
            ("target_id", json_str(target_id)),
            ("summary", json_str(summary)),
            ("details_json", json_str(details_json)),
        )
    )
    return hashlib.sha256(f"{prev_hash}\n{dump_json(value)}".encode()).hexdigest()
