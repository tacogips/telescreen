"""Parser for the bounded server-side issue search language."""

import re
import shlex
from datetime import date, datetime
from enum import StrEnum

from telescreen.core.vocabulary import (
    IssuePriority,
    IssueStatus,
    RuleClass,
    Severity,
    Visibility,
)
from telescreen.issues.query.model import (
    Comparison,
    ParsedQuery,
    QueryError,
    QueryErrorReason,
    QueryKey,
    QueryTerm,
)

MAX_QUERY_LENGTH: int = 1000
MAX_QUERY_TOKENS: int = 20
_DURATION: re.Pattern[str] = re.compile(r"^[+-][0-9]+[hdw]$")
_COMPARISON: re.Pattern[str] = re.compile(r"^(>=|>|<=|<)?(.+)$")


def parse_issue_query(text: str) -> ParsedQuery:
    """Parse a search query without retaining invalid user input in errors."""
    if len(text) > MAX_QUERY_LENGTH:
        raise QueryError(0, QueryErrorReason.TOO_LONG)
    try:
        tokens: list[str] = shlex.split(text)
    except ValueError:
        raise QueryError(0, QueryErrorReason.INVALID_VALUE) from None
    if len(tokens) > MAX_QUERY_TOKENS:
        raise QueryError(MAX_QUERY_TOKENS, QueryErrorReason.TOO_MANY_TOKENS)
    terms: list[QueryTerm] = []
    free_text: list[str] = []
    for index, token in enumerate(tokens):
        term: QueryTerm | None = _parse_token(token, index)
        if term is None:
            free_text.append(token)
        else:
            terms.append(term)
    return ParsedQuery(tuple(terms), tuple(free_text))


def _parse_token(token: str, index: int) -> QueryTerm | None:
    """Parse one token or return None when it is free text."""
    negated: bool = token.startswith("!")
    candidate: str = token[1:] if negated else token
    if ":" not in candidate:
        if negated:
            raise QueryError(index, QueryErrorReason.INVALID_VALUE)
        return None
    key_text: str
    raw_value: str
    key_text, raw_value = candidate.split(":", 1)
    try:
        key: QueryKey = QueryKey(key_text)
    except ValueError:
        raise QueryError(index, QueryErrorReason.UNKNOWN_KEY) from None
    if not raw_value:
        raise QueryError(index, QueryErrorReason.INVALID_VALUE)
    comparison: Comparison = Comparison.EQ
    value: str = raw_value
    if key in (QueryKey.TIMES_SEEN, QueryKey.VALUES, QueryKey.FIRST_SEEN, QueryKey.LAST_SEEN):
        match: re.Match[str] | None = _COMPARISON.fullmatch(raw_value)
        if match is None:
            raise QueryError(index, QueryErrorReason.INVALID_VALUE)
        operator: str = match.group(1) or ""
        value = match.group(2)
        comparison = _comparison(operator)
    values: tuple[str, ...] = _values(value, index)
    for item in values:
        if not _valid_value(key, item):
            raise QueryError(index, QueryErrorReason.INVALID_VALUE)
    return QueryTerm(key, values, negated, comparison)


def _values(value: str, index: int) -> tuple[str, ...]:
    """Read a scalar or comma-separated bracket value list."""
    if value.startswith("[") and value.endswith("]"):
        raw_values: tuple[str, ...] = tuple(part.strip() for part in value[1:-1].split(","))
        if not raw_values or any(not part for part in raw_values):
            raise QueryError(index, QueryErrorReason.INVALID_VALUE)
        return raw_values
    if "[" in value or "]" in value:
        raise QueryError(index, QueryErrorReason.INVALID_VALUE)
    return (value,)


def _valid_value(key: QueryKey, value: str) -> bool:
    """Validate a key's value against its closed vocabulary or grammar."""
    enum_type: type[StrEnum] | None = _enum_for(key)
    if enum_type is not None:
        try:
            enum_type(value)
            return True
        except ValueError:
            return False
    if key is QueryKey.IS:
        return value in (
            "unresolved",
            "for_review",
            "regressed",
            "escalating",
            "archived",
            "resolved",
            "pending_verification",
            "ignored",
            "allowed",
            "acknowledged",
            "in_progress",
            "assigned",
            "unassigned",
            "public",
        )
    if key is QueryKey.ASSIGNED:
        return value in ("none", "me", "my_teams") or value.startswith(("#", "member:", "team:"))
    if key is QueryKey.HAS:
        return value in ("assignee", "owner", "exception", "external_issue")
    if key is QueryKey.BOOKMARKS:
        return value == "me"
    if key in (QueryKey.TIMES_SEEN, QueryKey.VALUES):
        return value.isdecimal()
    if key in (QueryKey.FIRST_SEEN, QueryKey.LAST_SEEN):
        if _DURATION.fullmatch(value):
            return int(value[1:-1]) > 0
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                return True
            except ValueError:
                return False
    return bool(value)


def _enum_for(key: QueryKey) -> type[StrEnum] | None:
    """Return the closed vocabulary enum for a key."""
    if key is QueryKey.STATUS:
        return IssueStatus
    if key is QueryKey.SEVERITY:
        return Severity
    if key is QueryKey.PRIORITY:
        return IssuePriority
    if key is QueryKey.RULE:
        return RuleClass
    if key is QueryKey.EXPOSURE:
        return Visibility
    return None


def _comparison(operator: str) -> Comparison:
    """Convert an already-validated comparison symbol."""
    if operator == ">":
        return Comparison.GT
    if operator == ">=":
        return Comparison.GTE
    if operator == "<":
        return Comparison.LT
    if operator == "<=":
        return Comparison.LTE
    return Comparison.EQ
