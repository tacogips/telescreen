"""Safe exception summaries that never expose exception text."""

from dataclasses import dataclass

from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.vocabulary import ErrorCode


@dataclass(frozen=True)
class SafeError:
    """Public-safe error code and correlation identifier."""

    code: ErrorCode
    error_id: str


def safe_error_for(exc: BaseException) -> SafeError:
    """Preserve known application codes and hide all exception messages."""
    code: ErrorCode = exc.code if isinstance(exc, TelescreenError) else ErrorCode.INTERNAL_ERROR
    return SafeError(code, new_id())


def new_error_id() -> str:
    """Create a non-sensitive identifier for an error response."""
    return new_id()
