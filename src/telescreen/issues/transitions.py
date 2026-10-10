"""User-controlled issue status transitions."""

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, IssueStatus

USER_TRANSITIONS: tuple[tuple[IssueStatus, tuple[IssueStatus, ...]], ...] = (
    (
        IssueStatus.UNRESOLVED,
        (IssueStatus.ACKNOWLEDGED, IssueStatus.IN_PROGRESS),
    ),
    (
        IssueStatus.ACKNOWLEDGED,
        (
            IssueStatus.IN_PROGRESS,
            IssueStatus.UNRESOLVED,
        ),
    ),
    (IssueStatus.IN_PROGRESS, (IssueStatus.UNRESOLVED,)),
    (
        IssueStatus.REGRESSED,
        (
            IssueStatus.ACKNOWLEDGED,
            IssueStatus.IN_PROGRESS,
        ),
    ),
    (IssueStatus.PENDING_VERIFICATION, (IssueStatus.UNRESOLVED,)),
    (IssueStatus.RESOLVED, (IssueStatus.UNRESOLVED,)),
    (IssueStatus.IGNORED, (IssueStatus.UNRESOLVED,)),
)


def ensure_user_transition(current: IssueStatus, target: IssueStatus) -> None:
    """Raise a safe error if the requested user transition is not permitted."""
    allowed: tuple[IssueStatus, ...] = next(
        (targets for status, targets in USER_TRANSITIONS if status is current), ()
    )
    if target not in allowed:
        raise TelescreenError(
            ErrorCode.INVALID_TRANSITION, "issue status transition is not allowed"
        )
