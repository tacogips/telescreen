"""UTC clock interfaces and deterministic test clock."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Source of current time."""

    def now(self) -> datetime:
        """Return current time as an aware UTC datetime."""


@dataclass(frozen=True)
class SystemClock:
    """Clock backed by the system wall clock."""

    def now(self) -> datetime:
        """Return current UTC time."""
        return datetime.now(UTC)


@dataclass
class FakeClock:
    """Mutable clock for deterministic tests."""

    current: datetime

    def now(self) -> datetime:
        """Return the configured current time."""
        return ensure_utc(self.current)

    def advance(self, delta: timedelta) -> None:
        """Advance the clock by a duration."""
        self.current = ensure_utc(self.current) + delta

    def set(self, value: datetime) -> None:
        """Set the clock time."""
        self.current = ensure_utc(value)


def ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC, treating naive input as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
