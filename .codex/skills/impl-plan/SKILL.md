---
name: impl-plan
description: Use when creating execution plans from design documents. Provides plan structure, status tracking, dependencies, and progress logging guidelines for investigation or implementation work.
allowed-tools: Read, Write, Glob, Grep
---

# Execution Plan Skill

This skill provides guidelines for creating and managing execution plans from design documents.

## When to Apply

Apply this skill when:
- Translating design documents into actionable work plans
- Planning multi-session investigation or implementation work
- Breaking down large efforts into parallelizable workstreams
- Tracking progress across sessions

## Purpose

Execution plans bridge the gap between design documents and actual work. They provide:
- Clear deliverables with file paths or artifact targets
- Simple status tracking tables
- Checklist-based completion criteria
- Progress tracking across sessions

## Plan Granularity

Execution plans and spec files do not need 1:1 mapping.

| Mapping | When to Use |
|---------|-------------|
| **1:N** | Large specs should be split into smaller, focused plans |
| **N:1** | Closely related specs can be combined into one plan |
| **1:1** | Small, well-bounded work |

Recommended granularity:
- Each plan should be completable in 1-3 sessions
- Each plan should have 3-10 workstreams
- Keep parallelizable work clearly separated

## File Size Limits

Large plan files can make agent execution brittle
and hard to review.

| Metric | Limit |
|--------|-------|
| Line count | MAX 1000 lines |
| Workstreams per plan | MAX 8 |
| Tasks per plan | MAX 10 |

If a plan grows beyond these limits, split it by phase or topic. Keep plans readable while allowing realistic implementation detail.

## Output Location

All plans must live under `impl-plans/`:

```text
impl-plans/
├── README.md
├── active/
├── completed/
└── templates/
```

## Execution Plan Structure

Each plan must include:

1. Header with status, design reference, and dates
2. Design reference summary and scope boundaries
3. Workstreams with deliverables, validation, and checklists
4. Status table
5. Dependencies
6. Completion criteria
7. Progress log

## Workstream Format

Use a structure like:

```markdown
### 1. Source Inventory

**Deliverables**:
- `design-docs/references/README.md`
- `design-docs/specs/notes.md`

**Status**: NOT_STARTED

**Validation**:
- At least one primary source linked
- Scope boundaries written down

**Checklist**:
- [ ] Collect inputs
- [ ] Produce deliverables
- [ ] Validate outputs
- [ ] Record open questions
```

## Status Table

Use a compact overview:

```markdown
| Workstream | Deliverables | Status | Validation |
|------------|--------------|--------|------------|
| Source Inventory | `design-docs/references/README.md` | NOT_STARTED | Pending |
| Evidence Review | `design-docs/specs/notes.md` | NOT_STARTED | Pending |
```

## Completion Criteria

Use actionable checklist items such as:

```markdown
- [ ] All planned workstreams completed
- [ ] Validation steps completed
- [ ] Open issues documented
- [ ] Follow-up actions captured
```

## Writing Guidelines

- Prefer deliverables and validation over low-level detail
- Keep plans outcome-focused
- Use concrete file paths when possible
- Record blockers explicitly
- Avoid large copied source snippets unless needed for scope clarification

## Progress Log

Track session-by-session work:

```markdown
### Session: YYYY-MM-DD HH:MM
**Tasks Completed**: Added source inventory and initial findings
**Tasks In Progress**: Evidence review and gap analysis
**Blockers**: Need clarification on source scope
**Notes**: Key decisions made this session
```

## Quick Checklist

Before finalizing a plan, verify:
- [ ] Header has status, reference, and dates
- [ ] All workstreams have deliverables and validation
- [ ] Status table covers all workstreams
- [ ] Dependencies are listed
- [ ] Completion criteria are actionable
- [ ] Progress log section exists
- [ ] File is under 400 lines
