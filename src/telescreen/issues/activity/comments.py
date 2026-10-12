"""Comment persistence and authorization for issue activity."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import ActorKind, ErrorCode, IssueHistoryKind, SubscriptionReason
from telescreen.db.models import IssueCommentRow, IssueHistoryRow, IssueRow
from telescreen.issues.activity.mentions import add_mentions
from telescreen.issues.activity.subscriptions import auto_subscribe
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import ActorDirectory, ActorDisplay, RecipientResolver
from telescreen.ports.issues import IssueActivity, IssueActivityNotifier
from telescreen.ports.notifications import MemberNotifier


@dataclass(frozen=True)
class CommentService:
    """Manage redacted comments and their activity records."""

    redactor: Redactor
    audit: AuditLog
    clock: Clock
    notifier: MemberNotifier | None
    resolver: RecipientResolver
    actors: ActorDirectory | None = None
    activity: IssueActivityNotifier | None = None

    async def create(
        self, session: AsyncSession, issue_id: str, body: str, actor_ref: str
    ) -> IssueCommentRow:
        """Create a redacted comment and record its mentions."""
        issue: IssueRow = await self._issue(session, issue_id)
        redacted: str = self._validated_body(body)
        now: datetime = ensure_utc(self.clock.now())
        comment: IssueCommentRow = IssueCommentRow(
            id=new_id(), issue_id=issue_id, author_ref=actor_ref, body=redacted, created_at=now
        )
        session.add(comment)
        session.add(
            IssueHistoryRow(
                id=new_id(),
                issue_id=issue_id,
                kind=IssueHistoryKind.COMMENTED.value,
                actor_ref=actor_ref,
                to_value=comment.id,
                note=None,
                at=now,
            )
        )
        member_id: str | None = (
            actor_ref.removeprefix("member:") if actor_ref.startswith("member:") else None
        )
        if member_id is not None:
            await auto_subscribe(
                session, issue_id, member_id, SubscriptionReason.COMMENTED, self.clock
            )
        await add_mentions(
            session,
            issue_id,
            comment.id,
            redacted,
            member_id,
            await self._actor_display(session, actor_ref),
            f"TELE-{issue.number}",
            issue.display_title,
            self.resolver,
            self.notifier,
            self.clock,
        )
        await self._audit(session, actor_ref, "issue.commented", issue_id, "Comment added")
        if self.activity is not None:
            await self.activity.on_activity(
                session,
                IssueActivity(
                    IssueHistoryKind.COMMENTED,
                    issue_id,
                    actor_ref,
                    now,
                    comment_id=comment.id,
                ),
            )
        return comment

    async def edit(
        self,
        session: AsyncSession,
        issue_id: str,
        comment_id: str,
        body: str,
        actor_ref: str,
        can_manage_any: bool,
    ) -> IssueCommentRow:
        """Edit a comment and notify only newly added mentions."""
        comment: IssueCommentRow = await self._comment(session, issue_id, comment_id)
        self._authorize(comment, actor_ref, can_manage_any)
        issue: IssueRow = await self._issue(session, issue_id)
        redacted: str = self._validated_body(body)
        old_mentions: tuple[str, ...] = await self._mention_member_ids(session, comment.id)
        now: datetime = ensure_utc(self.clock.now())
        comment.body = redacted
        comment.edited_at = now
        session.add(
            IssueHistoryRow(
                id=new_id(),
                issue_id=issue_id,
                kind=IssueHistoryKind.COMMENT_EDITED.value,
                actor_ref=actor_ref,
                to_value=comment.id,
                note=None,
                at=now,
            )
        )
        await add_mentions(
            session,
            issue_id,
            comment.id,
            redacted,
            actor_ref.removeprefix("member:") if actor_ref.startswith("member:") else None,
            await self._actor_display(session, actor_ref),
            f"TELE-{issue.number}",
            issue.display_title,
            self.resolver,
            self.notifier,
            self.clock,
            old_mentions,
        )
        await self._audit(session, actor_ref, "issue.comment_edited", comment.id, "Comment edited")
        return comment

    async def delete(
        self,
        session: AsyncSession,
        issue_id: str,
        comment_id: str,
        actor_ref: str,
        can_manage_any: bool,
    ) -> IssueCommentRow:
        """Soft-delete a comment without retaining its text."""
        comment: IssueCommentRow = await self._comment(session, issue_id, comment_id)
        self._authorize(comment, actor_ref, can_manage_any)
        now: datetime = ensure_utc(self.clock.now())
        comment.body = ""
        comment.deleted_at = now
        session.add(
            IssueHistoryRow(
                id=new_id(),
                issue_id=issue_id,
                kind=IssueHistoryKind.COMMENT_DELETED.value,
                actor_ref=actor_ref,
                to_value=comment.id,
                note=None,
                at=now,
            )
        )
        await self._audit(
            session, actor_ref, "issue.comment_deleted", comment.id, "Comment deleted"
        )
        return comment

    def _validated_body(self, body: str) -> str:
        """Redact first and enforce the persisted comment size."""
        redacted: str = self.redactor.redact(body).text
        if not redacted.strip() or len(redacted) > 4000:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid comment")
        return redacted

    async def _issue(self, session: AsyncSession, issue_id: str) -> IssueRow:
        """Load a comment target issue."""
        issue: IssueRow | None = await session.get(IssueRow, issue_id)
        if issue is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        return issue

    async def _comment(
        self, session: AsyncSession, issue_id: str, comment_id: str
    ) -> IssueCommentRow:
        """Load a comment scoped to its issue."""
        comment: IssueCommentRow | None = await session.scalar(
            select(IssueCommentRow).where(
                IssueCommentRow.id == comment_id, IssueCommentRow.issue_id == issue_id
            )
        )
        if comment is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "comment not found")
        return comment

    def _authorize(self, comment: IssueCommentRow, actor_ref: str, can_manage_any: bool) -> None:
        """Require comment authorship or the manage-any permission."""
        if comment.author_ref != actor_ref and not can_manage_any:
            raise TelescreenError(ErrorCode.FORBIDDEN, "permission is not granted")

    async def _mention_member_ids(self, session: AsyncSession, comment_id: str) -> tuple[str, ...]:
        """Return prior member ids so edits can avoid notifying old mentions."""
        from telescreen.db.models import IssueMentionRow

        return tuple(
            await session.scalars(
                select(IssueMentionRow.member_id).where(IssueMentionRow.comment_id == comment_id)
            )
        )

    async def _actor_display(self, session: AsyncSession, actor_ref: str) -> str:
        """Resolve a human-readable actor without exposing internal identifiers."""
        if self.actors is not None:
            displays: tuple[ActorDisplay, ...] = await self.actors.display(session, (actor_ref,))
            return displays[0].display_name if displays else "Unknown"
        if actor_ref.startswith("system:"):
            return "Telescreen"
        return "Unknown"

    async def _audit(
        self, session: AsyncSession, actor_ref: str, action: str, target_id: str, summary: str
    ) -> None:
        """Append a content-free audit event."""
        kind: ActorKind = ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.TOKEN
        await self.audit.append(
            session,
            AuditEntry(
                kind,
                actor_ref,
                action,
                "issue_comment",
                target_id,
                summary,
                json_object((("target_id", json_str(target_id)),)),
            ),
        )
