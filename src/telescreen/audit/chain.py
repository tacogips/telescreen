"""Canonical audit-chain hashing."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from telescreen.core.clock import ensure_utc
from telescreen.core.json import JsonValue, dump_json, json_int, json_object, json_str
from telescreen.db.models.audit import AuditChainHeadRow, AuditRecordRow
from telescreen.db.models.service_audit import (
    AuditHeadModel,
    AuditRecordModel,
    InventoryAuditChainHeadRow,
    InventoryAuditRecordRow,
    NotificationAuditChainHeadRow,
    NotificationAuditRecordRow,
)

GENESIS_HASH: str = "0" * 64


@dataclass(frozen=True)
class AuditChainModels:
    """ORM row types that hold one store's append-only audit chain."""

    record: AuditRecordModel
    head: AuditHeadModel


class AuditHeadValue(Protocol):
    """Writable chain pointer fields shared by every store-specific ORM row."""

    id: int
    seq: int
    hash: str


class AuditRecordValue(Protocol):
    """Read-only audit record fields shared by each store's row model."""

    seq: int
    id: str
    at: datetime
    actor_kind: str
    actor_ref: str
    action: str
    target_kind: str
    target_id: str
    summary: str
    details_json: str
    prev_hash: str
    hash: str


MAIN_AUDIT_MODELS: AuditChainModels = AuditChainModels(AuditRecordRow, AuditChainHeadRow)
INVENTORY_AUDIT_MODELS: AuditChainModels = AuditChainModels(
    InventoryAuditRecordRow, InventoryAuditChainHeadRow
)
NOTIFICATION_AUDIT_MODELS: AuditChainModels = AuditChainModels(
    NotificationAuditRecordRow, NotificationAuditChainHeadRow
)


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
