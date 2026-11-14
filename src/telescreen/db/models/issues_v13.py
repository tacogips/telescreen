"""V1.3 issue collaboration, saved view and ownership database models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class SavedViewRow(Base, kw_only=True):
    """A redacted saved issue query owned by a member or shared with a team."""

    __tablename__ = "saved_views"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    sort: Mapped[str] = mapped_column(String(16))
    period: Mapped[str | None] = mapped_column(String(64), default=None)
    visibility: Mapped[str] = mapped_column(String(16))
    owner_member_id: Mapped[str] = mapped_column(String(36), index=True)
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SavedViewStarRow(Base, kw_only=True):
    """A member's star on a saved view."""

    __tablename__ = "saved_view_stars"

    view_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class SavedViewDefaultRow(Base, kw_only=True):
    """A member's default saved view selection."""

    __tablename__ = "saved_view_defaults"

    member_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    view_id: Mapped[str] = mapped_column(String(36))


class IssueCommentRow(Base, kw_only=True):
    """A redacted comment attached to an issue."""

    __tablename__ = "issue_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issue_id: Mapped[str] = mapped_column(String(36), index=True)
    author_ref: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class IssueMentionRow(Base, kw_only=True):
    """A member mention extracted from an issue comment."""

    __tablename__ = "issue_mentions"

    comment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IssueSubscriptionRow(Base, kw_only=True):
    """A member's issue subscription state and reason."""

    __tablename__ = "issue_subscriptions"

    issue_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subscribed: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IssueBookmarkRow(Base, kw_only=True):
    """A member's bookmark for an issue."""

    __tablename__ = "issue_bookmarks"

    issue_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExternalIssueLinkRow(Base, kw_only=True):
    """A redacted link from an issue to an external tracking item."""

    __tablename__ = "external_issue_links"
    __table_args__ = (UniqueConstraint("issue_id", "url"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issue_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(200))
    external_ref: Mapped[str | None] = mapped_column(String(256), default=None)
    created_by: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IssueMergeRow(Base, kw_only=True):
    """A merge or unmerge audit record for an issue pair."""

    __tablename__ = "issue_merges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    primary_issue_id: Mapped[str] = mapped_column(String(36), index=True)
    merged_issue_id: Mapped[str] = mapped_column(String(36), index=True)
    merged_by: Mapped[str] = mapped_column(String(256))
    merged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    unmerged_by: Mapped[str | None] = mapped_column(String(256), default=None)
    unmerged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class OwnershipRuleRow(Base, kw_only=True):
    """A configurable source and pattern for routing issues to a team."""

    __tablename__ = "ownership_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(32), default=None)
    pattern: Mapped[str] = mapped_column(String(512))
    team_id: Mapped[str] = mapped_column(String(36))
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_by: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
