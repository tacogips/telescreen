---
name: python-coding-standards
description: Use when writing, reviewing, or refactoring Python code. Provides typing, module layout, error handling, testing, and uv-based workflow guidance.
allowed-tools: Read, Grep, Glob
---

# Python Coding Standards

This skill provides practical Python guidance for this project.

## When to Apply

Apply these standards when:
- Writing new Python code
- Reviewing or refactoring existing modules
- Designing package APIs and CLI behavior
- Updating tests or project tooling

## Core Principles

1. Prefer clarity over clever abstractions.
2. Keep modules small and focused.
3. Type everything explicitly; see the strict typing rules below.
4. Make invalid states hard to represent and easy to test.

## Source File Size

- **Hard limit**: No Python source file (`*.py`) under `src/` or `tests/` should stay above **1000 lines**. If a file is at or past that size, **split it** in the same change set or as a focused follow-up.
- **How to split**: Prefer cohesive module boundaries such as CLI entry points, parsing, domain services, adapters, fixtures, or focused test modules. Keep import compatibility with thin facade modules when callers already depend on an established path.
- **Agents**: When editing or reviewing code, if a touched file is **1000+ lines**, treat splitting as **in scope** for the task unless the user explicitly excludes it.

## Project Layout

- Keep application code under `src/telescreen/`
- Keep tests under `tests/`
- Use `__main__.py` only as a thin entry point
- Put reusable logic in importable modules instead of inline CLI code

## Code Style

- Prefer `pathlib.Path` over raw path strings
- Model data and stateful services as dataclasses; closed value sets as enums
- Raise specific exceptions when failure needs to propagate
- Return simple value objects instead of overloaded tuples
- Keep side effects near the CLI boundary

## Typing (strict, enforced)

These rules are mandatory and are enforced by `ruff` (ANN, PGH), `ty check`
(warnings are errors), and the project AST linter `tools/typelint.py`
(`mise run typelint` / `uv run python tools/typelint.py`).

1. **Every declaration is typed.** All parameters (except `self`/`cls`), all
   return types (including `-> None`), and every variable binding: module,
   class and function locals use `name: T = value`. Tuple unpacking targets must
   be declared first (`a: int` / `b: str` then `a, b = pair`). Loop, `with`,
   `except`, walrus and match-capture targets are inferred and exempt, as are enum
   members and dunder attributes in a class body (`__tablename__ = "t"`,
   `__slots__ = ()`), whose types come from the base class. (TL001-TL003)
2. **Instance attributes are declared in the class body.** `self.x = ...` is
   only allowed when `x: T` is declared in the class body. (TL004)
3. **No `Any`, no `object`, no `dict`.** `Any`, `object`, `dict`, `Dict`,
   `defaultdict`, `OrderedDict`, `Mapping`, `MutableMapping` and `TypedDict` are
   forbidden in annotations, `cast()` and type aliases. Model data with
   `@dataclass` (stdlib or `pydantic.dataclasses`) and closed sets with `Enum` /
   `StrEnum`. Interfaces use `typing.Protocol`. (TL005)
4. **No `**kwargs`.** Neither `**kwargs` parameters nor `**mapping` call-site
   unpacking. Declare explicit keyword-only parameters. (TL006, TL007)
5. **Types are dataclasses or enums.** A class that declares typed fields must
   be a `@dataclass`, an `Enum`, a `Protocol`, an exception, or a SQLAlchemy
   `MappedAsDataclass` model. Stateful services are dataclasses too. (TL008)
6. **No implicit `Any` through bare generics.** `list`, `tuple`, `set`,
   `Callable`, `Sequence`, `Iterable`, ... always take type arguments;
   `Callable[..., T]` is forbidden (use explicit parameters or `ParamSpec`). (TL009)
7. **No suppression comments.** `# type: ignore`, `# ty: ignore`,
   `# pyright: ignore` are forbidden. (TL010)
8. Prefer `str | None`, PEP 695 generics (`def f[T](x: T) -> T`) and
   `type Alias = ...`. Avoid recursive type aliases (ty 0.0.x can hang on
   them); use a self-referencing `Protocol` or dataclass instead.

### External boundaries

Third-party APIs that speak in mappings (JSON, TOML, HTTP clients, Meilisearch,
SQL JSONB) are converted at the boundary:

- Parse inbound payloads directly into dataclasses (pydantic dataclasses /
  `TypeAdapter`) instead of holding intermediate mappings.
- Describe inbound mapping shapes with a structural `Protocol` (see
  `TomlTable` in `tools/typelint.py`) rather than `dict[str, Any]`.
- Serialize outbound with `dataclasses.asdict(...)` passed directly to the
  library call; do not bind the mapping to an annotated variable.
- If a boundary module genuinely cannot comply, add it to
  `[tool.typelint.per-file-ignores]` in `pyproject.toml` with a comment naming
  the boundary. This is the only exemption mechanism; there are no inline
  suppressions, and new entries must be justified in review.

## Testing

- Add or update tests for behavior changes
- Cover both success and failure paths for CLI-facing logic
- Prefer readable fixtures over heavy indirection

## Tooling

Set up once with `mise run setup` (installs mise tools, syncs ruff/ty/pytest via
`uv sync --dev`, installs pre-commit hooks). Run these before finishing changes:

```bash
uv run ruff format .
uv run ruff check .
uv run python tools/typelint.py
uv run ty check
uv run pytest
```

`mise run ci` runs all of them plus the build.

## Workflow

- Sync dependencies with `uv sync --dev`
- Run the package with `uv run python -m telescreen`
- Build distributions with `uv build`
