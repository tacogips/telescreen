"""Inventory-owned issue enrichment projection models."""

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class InventoryIssueLinkRow(Base, kw_only=True):
    """An issue's masked resource ownership and status projection."""

    __tablename__ = "inventory_issue_links"
    __table_args__ = (Index("ix_inventory_issue_links_resource_key", "resource_key"),)

    issue_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(64))
    short_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    owner_ref: Mapped[str | None] = mapped_column(String(256), default=None)
