---
name: design-doc
description: Use when creating or organizing design documents. Provides directory structure, file naming, and content guidelines for specs, research notes, references, and user questions.
allowed-tools: Read, Write, Glob, Grep
---

# Design Documentation Skill

This skill provides guidelines for creating and organizing design documents in this project.

## When to Apply

Apply this skill when:
- Creating new design documents
- Documenting research or investigation results
- Recording architectural or workflow decisions

## Output Location

All design documents must be stored under `design-docs/` subdirectories.

```text
design-docs/
├── specs/
│   ├── command.md
│   ├── architecture.md
│   ├── notes.md
│   └── design-*.md
├── references/
│   └── README.md
└── user-qa/
    └── README.md
```

## Directory Rules

| Directory | Purpose |
|-----------|---------|
| `design-docs/specs/` | Core specifications and operating notes |
| `design-docs/references/` | External references and source indexes |
| `design-docs/user-qa/` | Questions and pending decisions for the user |

Do not create markdown files directly under `design-docs/`.

## Specs Directory Structure

Use the three main category files:

| File | Purpose |
|------|---------|
| `command.md` | Repeatable commands, procedures, prompts, or browser flows |
| `architecture.md` | System boundaries, investigation scope, and major decisions |
| `notes.md` | Research findings, observations, and miscellaneous notes |

### Adding Content

1. Choose the appropriate category
2. Add a new section to the matching file
3. If the topic grows large, create a supporting `design-*.md` file and reference it

## User Q&A Directory

Use `design-docs/user-qa/` for:
- Questions requiring user input
- Ambiguous requirements needing clarification
- Pending design or scope decisions

### File Naming

| Prefix | Use Case |
|--------|----------|
| `qa-` | Questions or confirmations |
| `pending-` | Pending decisions |

## References Directory

All external references must be tracked in `design-docs/references/`.

When adding references:
1. Add the entry to `design-docs/references/README.md`
2. Create a topic subdirectory if needed
3. Prefer primary sources when possible

## Document Template

For supporting documents:

```markdown
# Document Title

Brief description of what this document covers.

## Overview

High-level summary.

## Details

Specific findings, design choices, or workflow notes.

## References

See `design-docs/references/README.md`.
```

## Content Guidelines

- Prioritize readability and traceability
- Keep copied code or command output minimal
- Prefer short excerpts over large pasted blocks
- Link findings back to sources whenever possible

## Quick Reference

| File | Content |
|------|---------|
| `command.md` | Commands, procedures, prompts, automation flows |
| `architecture.md` | Scope, boundaries, structure, major decisions |
| `notes.md` | Research findings and miscellaneous notes |
