"""Shared safe error types."""

from telescreen.core.vocabulary import ErrorCode


class TelescreenError(Exception):
    """An application error with a stable code and safe detail."""

    code: ErrorCode
    detail: str

    def __init__(self, code: ErrorCode, detail: str) -> None:
        """Create an error with a non-sensitive detail message."""
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


class ConfigError(TelescreenError):
    """A configuration error with a safe static detail."""

    def __init__(self, detail: str) -> None:
        """Create a configuration error."""
        super().__init__(ErrorCode.CONFIG_ERROR, detail)
