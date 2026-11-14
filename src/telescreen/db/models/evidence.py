"""Evidence metadata database model."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class EvidenceRow(Base, kw_only=True):
    """Metadata for scanned evidence without rejected file contents."""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issue_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    step_run_id: Mapped[str | None] = mapped_column(String(36), default=None)
    display_filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    storage_key: Mapped[str | None] = mapped_column(String(512), default=None)
    verdict: Mapped[str] = mapped_column(String(32))
    rejection_rule_classes: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
