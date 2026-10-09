"""SQLAlchemy declarative base shared by database models."""

from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass


class Base(MappedAsDataclass, DeclarativeBase, kw_only=True):
    """Dataclass-enabled declarative base for every database table."""
