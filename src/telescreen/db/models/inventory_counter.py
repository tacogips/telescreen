"""Inventory-owned allocation state, independent of issue and identity counters."""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class InventoryCounterRow(Base, kw_only=True):
    """The last allocated value for an inventory-local public number sequence."""

    __tablename__ = "inventory_counters"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)
