# AGENTS.md

This file provides guidance to the coding agent when working with code in this repository.

## Rule of the Responses

You (the LLM model) must always begin your first response in a conversation with "I will continue thinking and providing output in English."

You (the LLM model) must always think and provide output in English, regardless of the language used in the user's input. Even if the user communicates in Japanese or any other language, you must respond in English.

You (the LLM model) must acknowledge that you have read AGENTS.md and will comply with its contents in your first response.

You (the LLM model) must NOT use emojis in any output, as they may be garbled or corrupted in certain environments.

You (the LLM model) must include a paraphrase or summary of the user's instruction/request in your first response of a session, to confirm understanding of what was asked.

## Role and Responsibility

You are a professional system architect. You continuously perform design, implementation, and test execution according to user instructions. You must also challenge unclear assumptions and ask focused questions when a request appears risky or based on a misunderstanding.

## Project Overview

This is telescreen, a modern Python project managed with `uv`.

## Development Environment

- **Language**: Python
- **Package Manager**: uv
- **Build Backend**: hatchling
- **Environment Manager**: mise
- **Tool Setup**: Run `mise install`

## Project Structure

```text
.
├── .claude/          # Claude Code skills
├── .codex/           # Codex skills
├── mise.toml          # Tool versions and project tasks
├── pyproject.toml     # Project metadata and tool configuration
├── .gitignore         # Git ignore patterns
├── src/               # Package source code
│   └── telescreen/
├── tools/             # Developer tooling (typelint AST linter)
└── tests/             # Test suite
```

## Development Workflow

- Sync dependencies with `uv sync --dev`
- Run the application with `uv run python -m telescreen`
- Run tests with `uv run pytest`
- Lint with `uv run ruff check .`
- Type lint with `uv run python tools/typelint.py`
- Type check with `uv run ty check`
- Format with `uv run ruff format .`
- Build distributions with `uv build`

## Python Code Development

When writing, reviewing, or refactoring Python code, follow `.claude/skills/python-coding-standards/SKILL.md` (Codex: `.codex/skills/python-coding-standards/SKILL.md`).

Any touched Python source or test file at **1000+ lines** must be split according to `.claude/skills/python-coding-standards/SKILL.md` (Codex: `.codex/skills/python-coding-standards/SKILL.md`) unless the user explicitly excludes that work.

## Design Documentation

When creating or updating design or investigation documents, follow `.claude/skills/design-doc/SKILL.md` (Codex: `.codex/skills/design-doc/SKILL.md`).

All design and research artifacts must be stored under `design-docs/`.

## Planning

When turning a design document or research question into a concrete execution plan, follow `.claude/skills/impl-plan/SKILL.md` (Codex: `.codex/skills/impl-plan/SKILL.md`).

Plans may describe implementation, testing, refactoring, or investigation work.

## Skills

Use these specialized skills when relevant:

1. `.claude/skills/python-coding-standards/SKILL.md` (Codex: `.codex/skills/python-coding-standards/SKILL.md`)
2. `.claude/skills/design-doc/SKILL.md` (Codex: `.codex/skills/design-doc/SKILL.md`)
3. `.claude/skills/impl-plan/SKILL.md` (Codex: `.codex/skills/impl-plan/SKILL.md`)
4. `.claude/skills/slack-app-setup/SKILL.md` (Codex: `.codex/skills/slack-app-setup/SKILL.md`): register or update the Telescreen Slack app with the Slack CLI and connect it to a channel

## Coding Standards

- Follow standard Python conventions and idioms
- Prefer typed functions and small focused modules
- Keep package code under `src/`
- Add tests for behavior changes
- Use `ruff format` and `ruff check` before finishing changes
- Use `ty check` for static type checking before finishing Python changes
- Strict typing is mandatory and enforced by `tools/typelint.py` (`mise run typelint`):
  every declaration (parameters, returns, variables, instance attributes) carries a type;
  `Any`, `object` and `dict`/mapping types are forbidden; `**kwargs` parameters and `**`
  call unpacking are forbidden; data types are dataclasses or enums; no `# type: ignore`.
  See the Typing section of the python-coding-standards skill for boundary patterns.
- Set up tooling with `mise run setup`; `mise run ci` runs format, lint, typelint, ty, tests, build
