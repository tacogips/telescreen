"""Integration health check database model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class IntegrationCheckRow(Base, kw_only=True):
    """The latest health check result for one integration kind."""

    __tablename__ = "integration_checks"

    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(32), default=None)
