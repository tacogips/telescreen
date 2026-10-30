"""Generate Python semantics tables and parity fixtures for the Go worker."""

import argparse
import asyncio
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from telescreen.core.json import JsonValue, dump_json, json_int, json_object, json_str


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
        start: int
        value: int
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


def _fixture(
    name: str, members: tuple[tuple[str, JsonValue], ...], generated: bool = False
) -> GeneratedFile:
    version: str = ".".join(str(part) for part in sys.version_info[:3])
    meta: list[tuple[str, JsonValue]] = [
        ("python", json_str(version)),
        ("unidata", json_str(unicodedata.unidata_version)),
        ("generator", json_str("tools/gen_go_police_fixtures.py")),
    ]
    if generated:
        meta.append(("generated", json_int(2000)))
    body: str = dump_json(json_object(members))
    content: str = (
        '{"meta":'
        + dump_json(json_object(meta))
        + ',"obfuscation":"xor-v1:telescreen-go-parity-fixture",'
        + body[1:]
        + "\n"
    )
    return GeneratedFile(f"go/testdata/parity/{name}.json", content)


def render_outputs() -> tuple[GeneratedFile, ...]:
    """Render the complete Unicode tables and Python-reference parity fixtures."""
    root: str = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from go_police_fixture_cases import (
        detector_cases,
        exposure_cases,
        fingerprint_cases,
        line_segment_cases,
        locator_cases,
        matching_cases,
        redactor_cases,
        sealing_cases,
        sheet_segment_cases,
    )

    key: bytes = b"go-parity-fixture-key-0000000000000000"
    locator: JsonValue = asyncio.run(locator_cases())
    fingerprints: JsonValue = fingerprint_cases(key)
    fixtures: tuple[GeneratedFile, ...] = (
        _fixture("exposure", (("cases", exposure_cases()),)),
        _fixture("locators", tuple((member.name, member.value) for member in locator.members)),
        _fixture("matching", (("cases", matching_cases()),)),
        _fixture(
            "segments",
            (("lines", line_segment_cases()), ("sheets", asyncio.run(sheet_segment_cases()))),
        ),
        _fixture(
            "fingerprints", tuple((member.name, member.value) for member in fingerprints.members)
        ),
        _fixture("detector", (("cases", detector_cases()),), True),
        _fixture("sealing", (("cases", sealing_cases(key)),)),
        _fixture("redactor", (("cases", redactor_cases()),)),
    )
    return render_tables() + fixtures


def main() -> int:
    """Write or compare generated files, reporting differing paths only."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser()
    modes: argparse._MutuallyExclusiveGroup = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--write", action="store_true")
    modes.add_argument("--check", action="store_true")
    args: argparse.Namespace = parser.parse_args()
    failed: bool = False
    for output in render_outputs():
        path: Path = Path(output.path)
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output.content, encoding="utf-8")
        elif not path.exists() or path.read_bytes() != output.content.encode("utf-8"):
            print(output.path)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
