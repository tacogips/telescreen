"""Safe validators used by deterministic detection rules."""

import base64
import json
import math
import re
from collections import Counter


def luhn_valid(digits: str) -> bool:
    """Return whether a digit string passes the Luhn checksum."""
    total: int = 0
    for index, character in enumerate(reversed(digits)):
        digit: int = int(character)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return bool(digits) and total % 10 == 0


def known_card_iin(digits: str) -> bool:
    """Return whether a card number starts with a supported issuer range."""
    if digits.startswith("4"):
        return True
    if digits[:2] in ("34", "37") or digits.startswith(("6011", "65")):
        return True
    if len(digits) >= 2 and 51 <= int(digits[:2]) <= 55:
        return True
    if len(digits) >= 4 and 2221 <= int(digits[:4]) <= 2720:
        return True
    if len(digits) >= 4 and 3528 <= int(digits[:4]) <= 3589:
        return True
    if len(digits) >= 3 and 300 <= int(digits[:3]) <= 305:
        return True
    return digits.startswith(("36", "38"))


def my_number_valid(digits: str) -> bool:
    """Validate a 12-digit Japanese My Number check digit."""
    compact: str = re.sub(r"\D", "", digits)
    if len(compact) != 12:
        return False
    total: int = sum(
        int(digit) * (index + 2 if index < 6 else index - 4)
        for index, digit in enumerate(reversed(compact[:11]))
    )
    remainder: int = total % 11
    expected: int = 0 if remainder <= 1 else 11 - remainder
    return int(compact[-1]) == expected


def shannon_entropy(text: str) -> float:
    """Return Shannon entropy in bits per character."""
    if not text:
        return 0.0
    counts: Counter[str] = Counter(text)
    length: int = len(text)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def jwt_header_valid(token: str) -> bool:
    """Require the first JWT segment to decode to a JSON object with alg."""
    segments: list[str] = token.split(".")
    if len(segments) != 3:
        return False
    try:
        encoded: bytes = segments[0].encode("ascii")
        padding: bytes = b"=" * ((4 - len(encoded) % 4) % 4)
        decoded: bytes = base64.urlsafe_b64decode(encoded + padding)
        header: str = decoded.decode("utf-8")
        return isinstance(json.loads(header), dict) and "alg" in json.loads(header)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return False


def is_placeholder(value: str) -> bool:
    """Identify common example, masked, and environment-reference values."""
    lowered: str = value.strip().lower()
    return (
        lowered == "***"
        or lowered == "changeme"
        or lowered == "example"
        or (lowered.startswith("${") and lowered.endswith("}"))
        or (lowered.startswith("<") and lowered.endswith(">"))
    )
