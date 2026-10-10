"""Directory, team membership and API token database models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class MemberRow(Base, kw_only=True):
    """A Bureau user account."""

    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    handle: Mapped[str] = mapped_column(String(128), unique=True)
    display_name: Mapped[str] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    slack_user_id: Mapped[str | None] = mapped_column(String(128), default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TeamRow(Base, kw_only=True):
    """A responder team and its optional mailing destinations."""

    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    slack_channel: Mapped[str | None] = mapped_column(String(128), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lead_member_id: Mapped[str | None] = mapped_column(String(36), default=None)


class MemberIdentityRow(Base, kw_only=True):
    """A keyed provider identity associated with a Bureau member."""

    __tablename__ = "member_identities"
    __table_args__ = (UniqueConstraint("kind", "identity_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    identity_key: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TeamMembershipRow(Base, kw_only=True):
    """Membership relation between a team and a member."""

    __tablename__ = "team_memberships"

    team_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class ApiTokenRow(Base, kw_only=True):
    """A bearer token represented only by its hash."""

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    label: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[str] = mapped_column(String(512))
    member_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class CounterRow(Base, kw_only=True):
    """Named integer counters used for gapless issue numbers."""

    __tablename__ = "counters"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)
