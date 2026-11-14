"""Stable identifiers for grouped issues."""

import hashlib

from telescreen.core.vocabulary import RuleClass


def grouping_key(source: str, resource_key: str, rule_class: RuleClass) -> str:
    """Return the stable grouping digest for a source resource and rule class."""
    value: bytes = f"{source}\0{resource_key}\0{rule_class.value}".encode()
    return hashlib.sha256(value).hexdigest()


def short_id(number: int) -> str:
    """Format a public issue number."""
    return f"TELE-{number}"
