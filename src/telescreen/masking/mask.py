"""Render sensitive values according to their finding class."""

import re

from telescreen.core.vocabulary import RuleClass
from telescreen.detection.rules.catalog import PUBLIC_PREFIXES

_PEM_HEADER: re.Pattern[str] = re.compile(r"-----BEGIN [A-Z0-9 ]+-----")


def mask_value(value: str, rule_id: str, rule_class: RuleClass) -> str:
    """Return a persistable representation of a detected value."""
    if rule_class in (RuleClass.CREDENTIAL, RuleClass.TOKEN):
        prefix: str | None = next(
            (
                candidate
                for candidate in sorted(PUBLIC_PREFIXES, key=len, reverse=True)
                if value.startswith(candidate)
            ),
            None,
        )
        if prefix is not None:
            secret_length: int = len(value) - len(prefix)
            return f"{prefix}{'*' * min(secret_length, 16)}[{len(value)}]"
        return f"********[{len(value)}]"
    if rule_class is RuleClass.PRIVATE_KEY:
        lines: list[str] = value.splitlines()
        header: str = lines[0].strip() if lines else ""
        if _PEM_HEADER.fullmatch(header):
            return f"{header} [REDACTED {len(value)} chars]"
        return f"[REDACTED PRIVATE KEY {len(value)} chars]"
    if rule_class is RuleClass.PASSWORD:
        return f"********[{len(value)}]"
    if rule_class is RuleClass.PAYMENT_CARD:
        digits: str = re.sub(r"\D", "", value)
        return f"**** **** **** {digits[-4:]}"
    if rule_class is RuleClass.NATIONAL_ID:
        return "*" * 12
    if rule_class is RuleClass.EMAIL:
        if "@" not in value:
            return f"********[{len(value)}]"
        local: str
        domain: str
        local, domain = value.rsplit("@", 1)
        return f"{local[:1]}**\u2060*@{domain}"
    if rule_class is RuleClass.PHONE:
        digits_seen: int = 0
        total_digits: int = sum(character.isdigit() for character in value)
        output: list[str] = []
        for character in value:
            if character.isdigit():
                digits_seen += 1
                output.append(character if digits_seen > total_digits - 2 else "*")
            else:
                output.append(character)
        return "".join(output)
    if rule_class is RuleClass.ADDRESS:
        return "[ADDRESS REDACTED]"
    return f"********[{len(value)}]"
