"""Generate Python semantics tables and parity fixtures for the Go worker."""

import re
import sys
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class GeneratedFile:
    """An output path and its deterministic text."""

    path: str
    content: str


def _ranges(points: list[int]) -> list[str]:
    lines: list[str] = []
    if not points:
        return lines
    start: int = points[0]
    end: int = start
    for point in points[1:]:
        if point == end + 1:
            end = point
        else:
            lines.append(f"{start:04X}-{end:04X}")
            start = end = point
    lines.append(f"{start:04X}-{end:04X}")
    return lines


def render_tables() -> tuple[GeneratedFile, ...]:
    """Evaluate Python's character semantics for all Unicode scalar values."""
    version: str = ".".join(str(part) for part in sys.version_info[:3])
    header: str = f"# python={version} unidata={unicodedata.unidata_version}\n"
    word: list[int] = []
    digit: list[int] = []
    space: list[int] = []
    linebreak: list[int] = []
    decimal: list[tuple[int, int]] = []
    lower: list[str] = []
    casefold: list[str] = []
    folds: list[list[int]] = [[] for _ in range(26)]
    word_re: re.Pattern[str] = re.compile(r"\w")
    space_re: re.Pattern[str] = re.compile(r"\s")
    decimal_re: re.Pattern[str] = re.compile(r"\d")
    ascii_re: re.Pattern[str] = re.compile(r"[A-Za-z]", re.IGNORECASE)
    letter_res: list[re.Pattern[str]] = [re.compile(chr(97 + i), re.IGNORECASE) for i in range(26)]
    for code in range(0x110000):
        if 0xD800 <= code <= 0xDFFF:
            continue
        char: str = chr(code)
        if word_re.fullmatch(char):
            word.append(code)
        if char.isdigit():
            digit.append(code)
        is_space: bool = space_re.fullmatch(char) is not None
        assert is_space == char.isspace()
        if is_space:
            space.append(code)
        if len(("a" + char + "b").splitlines()) == 2:
            linebreak.append(code)
        if decimal_re.fullmatch(char):
            decimal.append((code, unicodedata.decimal(char)))
        for mapped, target in ((char.lower(), lower), (char.casefold(), casefold)):
            if mapped != char:
                target.append(f"{code:04X}=" + " ".join(f"{ord(c):04X}" for c in mapped))
        matches: list[int] = [i for i, pattern in enumerate(letter_res) if pattern.fullmatch(char)]
        assert bool(matches) == (ascii_re.fullmatch(char) is not None)
        for index in matches:
            folds[index].append(code)
    decimal_lines: list[str] = []
    if decimal:
        start, value = decimal[0]
        end: int = start
        last_value: int = value
        for code, current in decimal[1:]:
            if code == end + 1 and current == last_value + 1:
                end = code
            else:
                decimal_lines.append(f"{start:04X}-{end:04X} {value}")
                start, value, end = code, current, code
            last_value = current
        decimal_lines.append(f"{start:04X}-{end:04X} {value}")
    names: tuple[str, ...] = (
        "word",
        "digit",
        "space",
        "linebreak",
        "decimal",
        "lower",
        "casefold",
        "ignorecase",
    )
    data: tuple[list[str], ...] = (
        _ranges(word),
        _ranges(digit),
        _ranges(space),
        _ranges(linebreak),
        decimal_lines,
        lower,
        casefold,
        [
            chr(97 + i) + "=" + " ".join(f"{code:04X}" for code in points)
            for i, points in enumerate(folds)
        ],
    )
    return tuple(
        GeneratedFile(f"go/internal/pytext/data/{name}.txt", header + "\n".join(lines) + "\n")
        for name, lines in zip(names, data, strict=True)
    )
