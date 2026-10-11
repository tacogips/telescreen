# User Q&A

This directory contains items requiring user confirmation or decision.

## Purpose

Store questions, pending decisions, and items awaiting user approval.

## File Naming Convention

| Prefix | Use Case |
|--------|----------|
| `qa-` | Questions/confirmation items |
| `pending-` | Pending decisions |

## Current Items

- [qa-example.md](./qa-example.md) - Example: Database Selection (template example)
- [pending-example.md](./pending-example.md) - Example: CLI Output Format (template example)
- [qa-ui-authentication.md](./qa-ui-authentication.md) - UI/API authentication model (resolved: bearer API tokens; OIDC later, now decided in qa-oidc-sso.md)
- [qa-oidc-sso.md](./qa-oidc-sso.md) - v1.2 OIDC SSO, role claim mapping, provisioning, UI session (decided: design defaults, configurable)
- [qa-evidence-file-types.md](./qa-evidence-file-types.md) - Evidence file types (resolved: text and images; images scanned by vision managed LLM)
- [qa-llm-data-egress.md](./qa-llm-data-egress.md) - Managed LLM provider and egress (resolved: Claude default, Codex selectable; text detection opt-in)

- [qa-credential-validity-check.md](./qa-credential-validity-check.md) - Provider-side credential validity checks (resolved: rejected)
- [qa-on-demand-reveal.md](./qa-on-demand-reveal.md) - Live reveal of matched values (resolved: rejected; deep links only)
- [qa-automated-remediation-scopes.md](./qa-automated-remediation-scopes.md) - Write scopes for automated remediation (resolved: rejected; read-only)

## Adding New Items

1. Create a new file with appropriate prefix (`qa-` or `pending-`)
2. Include clear description of the question or decision needed
3. List available options if applicable
4. Update this README.md with a reference to the new item
