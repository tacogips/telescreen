"""Compiled rule catalog for credentials and personal information."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from telescreen.core.vocabulary import RuleClass
from telescreen.detection.rules.validators import (
    is_placeholder,
    jwt_header_valid,
    known_card_iin,
    luhn_valid,
    my_number_valid,
    shannon_entropy,
)


@dataclass(frozen=True)
class RuleSpec:
    """One compiled detector rule and its validation policy."""

    rule_id: str
    rule_class: RuleClass
    pattern: re.Pattern[str]
    value_group: int
    validator: Callable[[str], bool] | None
    keywords: tuple[str, ...]
    keyword_window: int
    confidence: float


def _card_valid(value: str) -> bool:
    """Validate card checksum and issuer prefix."""
    digits: str = re.sub(r"\D", "", value)
    return luhn_valid(digits) and known_card_iin(digits)


def _aws_secret_valid(value: str) -> bool:
    """Require high entropy in an AWS secret candidate."""
    return shannon_entropy(value) >= 4.0


def _jwt_valid(value: str) -> bool:
    """Validate the JWT header without exposing token content."""
    return jwt_header_valid(value)


def _password_valid(value: str) -> bool:
    """Reject short and common placeholder passwords."""
    return len(value) >= 8 and not is_placeholder(value)


def _my_number_valid(value: str) -> bool:
    """Validate a candidate My Number after removing separators."""
    return my_number_valid(value)


RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        "aws_access_key_id",
        RuleClass.CREDENTIAL,
        re.compile(r"\b((?:AKIA|ASIA)[A-Z0-9]{16})\b"),
        1,
        None,
        (),
        0,
        0.99,
    ),
    RuleSpec(
        "aws_secret_access_key",
        RuleClass.CREDENTIAL,
        re.compile(r"\b([A-Za-z0-9/+=]{40})\b"),
        1,
        _aws_secret_valid,
        ("aws_secret", "secret_access_key"),
        80,
        0.92,
    ),
    RuleSpec(
        "github_token",
        RuleClass.TOKEN,
        re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{82})\b"),
        1,
        None,
        (),
        0,
        0.99,
    ),
    RuleSpec(
        "slack_token",
        RuleClass.TOKEN,
        re.compile(
            r"\b(xox[abprs]-[A-Za-z0-9-]{10,}|https://hooks\.slack\.com/services/[A-Za-z0-9/]+)"
        ),
        1,
        None,
        (),
        0,
        0.98,
    ),
    RuleSpec(
        "google_api_key",
        RuleClass.CREDENTIAL,
        re.compile(r"\b(AIza[0-9A-Za-z_-]{28,35})\b"),
        1,
        None,
        (),
        0,
        0.99,
    ),
    RuleSpec(
        "private_key",
        RuleClass.PRIVATE_KEY,
        re.compile(
            r"(-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----"
            r"[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----)"
        ),
        1,
        None,
        (),
        0,
        1.0,
    ),
    RuleSpec(
        "jwt",
        RuleClass.TOKEN,
        re.compile(r"\b(eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b"),
        1,
        _jwt_valid,
        (),
        0,
        0.95,
    ),
    RuleSpec(
        "password_assignment",
        RuleClass.PASSWORD,
        re.compile(
            r"(?:password|passwd|pwd|secret|pass|パスワード)\s*[:=]\s*([^\s,;]{8,})", re.IGNORECASE
        ),
        1,
        _password_valid,
        (),
        0,
        0.95,
    ),
    RuleSpec(
        "credit_card",
        RuleClass.PAYMENT_CARD,
        re.compile(r"\b((?:\d[ -]?){12,18}\d)\b"),
        1,
        _card_valid,
        (),
        0,
        0.985,
    ),
    RuleSpec(
        "my_number",
        RuleClass.NATIONAL_ID,
        re.compile(r"(?<!\d)(\d{4}[- ]?\d{4}[- ]?\d{4})(?!\d)"),
        1,
        _my_number_valid,
        (),
        0,
        0.98,
    ),
    RuleSpec(
        "email",
        RuleClass.EMAIL,
        re.compile(
            r"\b([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z]{2,63})+)\b"
        ),
        1,
        None,
        (),
        0,
        0.9,
    ),
    RuleSpec(
        "phone_jp",
        RuleClass.PHONE,
        re.compile(r"(?<!\d)(0(?:\d{1,4}-\d{1,4}-\d{4}|\d{9,10}|[5789]0-?\d{4}-?\d{4}))(?!\d)"),
        1,
        None,
        (),
        0,
        0.8,
    ),
    RuleSpec(
        "phone_intl",
        RuleClass.PHONE,
        re.compile(r"(?<!\w)(\+\d{1,3}(?:[ -]?\d){7,14})(?!\d)"),
        1,
        None,
        (),
        0,
        0.78,
    ),
    RuleSpec(
        "address_jp",
        RuleClass.ADDRESS,
        re.compile(
            r"((?:(?:東京都|北海道|(?:京都|大阪)府|[一-龯]{2,3}県)[一-龯ぁ-んァ-ヶー]{2,12}(?:市|区|町|村)[^\n]{0,24}?\d{1,4}(?:丁目|番地|[-－]\d{1,4}(?:[-－]\d{1,4})?))|(?:〒\d{3}-\d{4}[^\n]{0,40}))"
        ),
        1,
        None,
        (),
        0,
        0.6,
    ),
    RuleSpec(
        "address_en",
        RuleClass.ADDRESS,
        re.compile(
            r"\b(\d{1,6}\s+[A-Za-z0-9.' -]{2,50}\s"
            r"(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Lane|Ln)\b)",
            re.IGNORECASE,
        ),
        1,
        None,
        (),
        0,
        0.5,
    ),
)

PUBLIC_PREFIXES: tuple[str, ...] = (
    "AKIA",
    "ASIA",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "xoxa-",
    "xoxb-",
    "xoxp-",
    "xoxr-",
    "xoxs-",
    "AIza",
)
