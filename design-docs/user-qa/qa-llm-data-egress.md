# Managed LLM Detection and Data Egress

**Status**: Resolved (2026-10-08, decided by the repository owner)

**Created**: 2026-10-08

**Category**: Architecture

## Decision Needed

May monitored content be sent to the managed LLM provider for detection?

## Background

Requirement 10 allows detection by a managed LLM. Detection needs the content itself, so
enabling `managed_llm` in `TELESCREEN_DETECTORS` sends scoped monitored content (not stored
by Telescreen) to the provider. `ai_agent` workflow steps and Assistant chat only send
masked data.

## Alternatives

### Option A: Opt-in, rule-only default (v1 default)

- `TELESCREEN_DETECTORS=rule` by default; operators opt in to `managed_llm` or `local_llm`.
- `cascade` mode limits LLM input to windows around rule candidates.

### Option B: Managed LLM enabled by default

- Better recall on unstructured PII; sends content off-premises by default.

## Impact

`design-docs/specs/design-detectors.md` (Composite and configuration), README.

## Decision

The managed LLM provider is selectable between Codex (OpenAI API) and Claude (Anthropic API); the default is Claude (`TELESCREEN_MANAGED_LLM_PROVIDER=claude`). The opt-in default for text detection (`TELESCREEN_DETECTORS=rule`) stands; image evidence scanning uses the managed LLM by design (owner accepted image evidence). See `design-docs/specs/design-detectors.md` (LLM harness).
