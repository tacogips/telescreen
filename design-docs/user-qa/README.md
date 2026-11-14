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
- [pending-custom-push-resource-identity.md](./pending-custom-push-resource-identity.md) - `/scan` and `/findings` key the same custom locator differently (pending; v1.4 keeps the current behavior)
- [pending-service-split-tradeoffs.md](./pending-service-split-tradeoffs.md) - Service split defaults: replicated API tokens, shared identity key, pending receipts, CLI audit path, two topologies (pending owner review; defaults apply)
- [pending-go-gdrive-police-contract-gaps.md](./pending-go-gdrive-police-contract-gaps.md) - Go Drive Police: contract 1.2 gaps (skipped channel, owner/placement, 1 MiB body, Redactor on hex fingerprints, 1000-locator cap), changes feed not ported, 410 is a fetch failure like Python (owner decided V1; rest pending owner review, defaults apply)

## Adding New Items

1. Create a new file with appropriate prefix (`qa-` or `pending-`)
2. Include clear description of the question or decision needed
3. List available options if applicable
4. Update this README.md with a reference to the new item
