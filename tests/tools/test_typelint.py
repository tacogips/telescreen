"""Tests for the project AST type-discipline linter (tools/typelint.py)."""

import textwrap
import tomllib
from pathlib import Path

import pytest

from typelint import (
    Config,
    Rule,
    Violation,
    config_from_table,
    iter_python_files,
    lint_files,
    lint_source,
    load_config,
    main,
)


def rules_of(source: str) -> list[Rule]:
    violations: list[Violation] = lint_source(textwrap.dedent(source))
    return [violation.rule for violation in violations]


def test_clean_module_has_no_violations() -> None:
    source: str = """
        from dataclasses import dataclass
        from enum import StrEnum

        class Color(StrEnum):
            RED = "red"

        @dataclass(frozen=True)
        class Point:
            x: int
            y: int

            def shifted(self, dx: int) -> "Point":
                moved: int = self.x + dx
                return Point(moved, self.y)

        LIMIT: int = 3

        def total(values: list[int], *rest: int, scale: float = 1.0) -> float:
            result: float = 0.0
            for value in values:
                result += value * scale
            return result + sum(rest)
    """
    assert rules_of(source) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f(x) -> None: ...", [Rule.MISSING_PARAM_ANNOTATION]),
        ("def f(x: int): ...", [Rule.MISSING_RETURN_ANNOTATION]),
        ("x = 1", [Rule.UNANNOTATED_VARIABLE]),
        ("def f() -> None:\n    a, b = 1, 2", [Rule.UNANNOTATED_VARIABLE] * 2),
        ("def f(**kwargs: int) -> None: ...", [Rule.FORBIDDEN_KWARGS_PARAMETER]),
        ("def f(o: int) -> None:\n    g(**o)", [Rule.FORBIDDEN_KWARGS_UNPACK]),
        ("def f(o: dict[str, int]) -> None: ...", [Rule.FORBIDDEN_TYPE]),
        ("def f(o: object) -> None: ...", [Rule.FORBIDDEN_TYPE]),
        ("from typing import Any", [Rule.FORBIDDEN_TYPE]),
        ("import typing\nv: typing.Any = 1", [Rule.FORBIDDEN_TYPE]),
        ("v: 'Mapping[str, int]' = m", [Rule.FORBIDDEN_TYPE]),
        ("v: list = []", [Rule.BARE_GENERIC]),
        ("from collections.abc import Callable\nv: Callable[..., int] = f", [Rule.BARE_GENERIC]),
        ("v: int = 1  # type: ignore[assignment]", [Rule.TYPE_SUPPRESSION_COMMENT]),
        ("v: int = 1  # ty: ignore", [Rule.TYPE_SUPPRESSION_COMMENT]),
        ("class P:\n    x: int", [Rule.CLASS_NOT_DATACLASS]),
    ],
)
def test_each_rule_is_reported(source: str, expected: list[Rule]) -> None:
    assert rules_of(source) == expected


def test_undeclared_self_attribute_is_reported() -> None:
    source: str = """
        from dataclasses import dataclass

        @dataclass
        class Box:
            size: int

            def grow(self) -> None:
                self.size += 1
                self.label = "x"
    """
    assert rules_of(source) == [Rule.UNDECLARED_INSTANCE_ATTRIBUTE]


def test_unpacking_into_predeclared_names_is_allowed() -> None:
    source: str = """
        def f() -> int:
            a: int
            b: int
            a, b = 1, 2
            return a + b
    """
    assert rules_of(source) == []


def test_allowed_bases_resolve_transitively() -> None:
    source: str = """
        from typing import Protocol

        class TelescreenError(Exception):
            code: int

        class LeakError(TelescreenError):
            path: str

        class Reader(Protocol):
            name: str
    """
    assert rules_of(source) == []


def test_annotated_metadata_and_literal_values_are_not_types() -> None:
    source: str = """
        from typing import Annotated, Literal

        def f(q: Annotated[str, Doc("dict of things")], m: Literal["object"]) -> None: ...
    """
    assert rules_of(source) == []


def test_typevar_and_enum_members_need_no_annotation() -> None:
    source: str = """
        from enum import Enum
        from typing import NewType

        UserId = NewType("UserId", str)

        class Level(Enum):
            LOW = 1
            HIGH = 2
    """
    assert rules_of(source) == []


def test_per_file_ignores_from_pyproject(tmp_path: Path) -> None:
    pyproject: str = textwrap.dedent(
        """
        [tool.typelint]
        paths = ["pkg"]

        [tool.typelint.per-file-ignores]
        "pkg/boundary.py" = ["TL005"]
        """
    )
    config: Config = config_from_table(tomllib.loads(pyproject))
    package: Path = tmp_path / "pkg"
    package.mkdir()
    boundary: Path = package / "boundary.py"
    boundary.write_text("def f(o: dict[str, int]) -> None: ...\n", encoding="utf-8")
    other: Path = package / "other.py"
    other.write_text("def f(o: dict[str, int]) -> None: ...\n", encoding="utf-8")

    violations: list[Violation] = lint_files([boundary, other], config, tmp_path)

    assert [(v.path.name, v.rule) for v in violations] == [("other.py", Rule.FORBIDDEN_TYPE)]


def test_main_returns_nonzero_on_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "bad.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["bad.py"]) == 1
    (tmp_path / "good.py").write_text("x: int = 1\n", encoding="utf-8")
    assert main(["good.py"]) == 0


def test_repository_is_clean() -> None:
    repository_root: Path = Path(__file__).resolve().parents[2]
    config: Config = load_config(repository_root / "pyproject.toml")
    files: list[Path] = list(
        iter_python_files([repository_root / p for p in config.paths], config, repository_root)
    )
    violations: list[Violation] = lint_files(files, config, repository_root)
    assert [v.render() for v in violations] == []


def test_class_body_dunder_attributes_need_no_annotation() -> None:
    source: str = """
        from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass

        class Base(MappedAsDataclass, DeclarativeBase):
            pass

        class Issue(Base):
            __tablename__ = "issues"
            __table_args__ = ()

            id: Mapped[str]
    """
    assert rules_of(source) == []


def test_module_level_dunder_and_class_non_dunder_still_need_annotation() -> None:
    source: str = """
        __all__ = ["x"]

        class Box:
            __slots__ = ()
            size = 1
    """
    assert rules_of(source) == [Rule.UNANNOTATED_VARIABLE, Rule.UNANNOTATED_VARIABLE]
