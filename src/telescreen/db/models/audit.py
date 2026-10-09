"""Append-only audit record and chain head database models."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class AuditRecordRow(Base, kw_only=True):
    """A sequence-numbered hash-chained audit entry."""

    __tablename__ = "audit_records"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    id: Mapped[str] = mapped_column(String(36), unique=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_kind: Mapped[str] = mapped_column(String(32))
    actor_ref: Mapped[str] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(128))
    target_kind: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(36))
    summary: Mapped[str] = mapped_column(Text)
    details_json: Mapped[str] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditChainHeadRow(Base, kw_only=True):
    """The locked pointer to the latest audit chain record."""

    __tablename__ = "audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    hash: Mapped[str] = mapped_column(String(64))
