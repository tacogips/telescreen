"""Typed query language values for issue stream searches."""

from dataclasses import dataclass
from enum import StrEnum


class QueryKey(StrEnum):
    """Supported structured issue query keys."""

    IS = "is"
    STATUS = "status"
    SEVERITY = "severity"
    PRIORITY = "priority"
    RULE = "rule"
    SOURCE = "source"
    EXPOSURE = "exposure"
    ASSIGNED = "assigned"
    HAS = "has"
    FIRST_SEEN = "first_seen"
    LAST_SEEN = "last_seen"
    TIMES_SEEN = "times_seen"
    VALUES = "values"
    BOOKMARKS = "bookmarks"


class Comparison(StrEnum):
    """Comparison operators supported by numeric and date fields."""

    EQ = "eq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class QueryErrorReason(StrEnum):
    """Safe parser failure classifications."""

    UNKNOWN_KEY = "unknown_key"
    INVALID_VALUE = "invalid_value"
    TOO_MANY_TOKENS = "too_many_tokens"
    TOO_LONG = "too_long"


class TrendBucket(StrEnum):
    """Supported per-issue trend windows."""

    HOURLY_24 = "24h"
    DAILY_14 = "14d"


@dataclass(frozen=True)
class QueryTerm:
    """One structured query expression."""

    key: QueryKey
    values: tuple[str, ...]
    negated: bool
    comparison: Comparison


@dataclass(frozen=True)
class ParsedQuery:
    """Parsed structured terms and unstructured text terms."""

    terms: tuple[QueryTerm, ...]
    text: tuple[str, ...]


class QueryError(ValueError):
    """Parser failure carrying only a token position and safe reason."""

    token_index: int
    reason: QueryErrorReason

    def __init__(self, token_index: int, reason: QueryErrorReason) -> None:
        """Create a safe query parse error."""
        self.token_index = token_index
        self.reason = reason
        super().__init__(reason.value)
