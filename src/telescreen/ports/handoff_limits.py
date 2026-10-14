"""Transport-independent bounds shared by durable handoff codecs."""

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode

MAX_HANDOFF_BYTES: int = 16 * 1024 * 1024
MAX_INVENTORY_ENTRIES: int = 100_000


def require_payload_size(payload: str) -> None:
    """Reject excessive UTF-8 wire data before parsing or persisting it."""
    if len(payload) > MAX_HANDOFF_BYTES or len(payload.encode("utf-8")) > MAX_HANDOFF_BYTES:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "handoff payload exceeds supported limit")
