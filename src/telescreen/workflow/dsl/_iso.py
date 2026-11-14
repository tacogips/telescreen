"""ISO-8601 duration formatting for persisted workflow values."""

from __future__ import annotations

from datetime import timedelta


def format_duration(value: timedelta) -> str:
    """Format a duration without losing fractional seconds."""
    if value == timedelta(0):
        return "PT0S"
    date_part: str = f"{value.days}D" if value.days else ""
    hours: int = value.seconds // 3600
    minutes: int = value.seconds % 3600 // 60
    seconds: int = value.seconds % 60
    fraction: str = f"{value.microseconds:06d}".rstrip("0")
    second_part: str = str(seconds)
    if value.microseconds:
        second_part += f".{fraction}"
    time_part: str = "".join(
        (
            f"{hours}H" if hours else "",
            f"{minutes}M" if minutes else "",
            f"{second_part}S" if seconds or value.microseconds else "",
        )
    )
    if not time_part:
        return f"P{date_part}"
    return f"P{date_part}T{time_part}"
