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
3. Use explicit typing for public functions and important internal boundaries.
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
- Prefer dataclasses or small classes only when they simplify state management
- Raise specific exceptions when failure needs to propagate
- Return simple value objects instead of overloaded tuples
- Keep side effects near the CLI boundary

## Typing

- Add return types to public functions
- Use concrete collection types where practical
- Prefer `str | None` style syntax for optional values
- Avoid `Any` unless a boundary genuinely requires it

## Testing

- Add or update tests for behavior changes
- Cover both success and failure paths for CLI-facing logic
- Prefer readable fixtures over heavy indirection

## Tooling

Run these before finishing changes:

```bash
uv run ruff format .
uv run ruff check .
uv run pytest
```

## Workflow

- Sync dependencies with `uv sync --dev`
- Run the package with `uv run python -m telescreen`
- Build distributions with `uv build`
