# Command Design

This document describes the `telescreen` CLI, configuration environment variables, exit
codes and the v1 dependency set.

## Overview

`telescreen` (entry point `telescreen.cli:main`, also `python -m telescreen`) uses argparse
subcommands. Every subcommand first calls `configure_logging()` (masking formatter) and
`load_settings(os.environ)`. Configuration comes only from environment variables; secrets
are secret references (`env:NAME` or `file:/path`).

---

## Sections

### Subcommands

| Command | Description |
|---------|-------------|
| `telescreen serve [--host 0.0.0.0] [--port 8000]` | Run the Reporting Bureau API (uvicorn) |
| `telescreen worker [--once]` | Run the engine worker and scheduler; `--once` processes ready tasks then exits |
| `telescreen migrate [--revision head]` | Apply Alembic migrations |
| `telescreen workflow validate FILE...` | Offline validation of workflow YAML/JSON; prints errors with JSON paths |
| `telescreen collector run NAME [--watch]` | Enqueue an immediate scan of a collector config; `--watch` (gdrive kinds) registers the Drive push channel |
| `telescreen collector sweep NAME` | Start a concurrent map-reduce sweep of a collector config's whole estate and print the map job id (exit 1 with `conflict` if a sweep of that config is still active) |
| `telescreen police list` | List registered police with mode, labels and heartbeat age |
| `telescreen token create --label TEXT --scopes read,write [--member HANDLE]` | Create an API token and print it once. v1.2 adds `--member`, which binds the token to a member, so its permissions are the scopes intersected with the member's role |
| `telescreen member set-role HANDLE admin\|responder\|viewer\|none` | v1.2. Set a member's role (audited with source `cli`). Demoting the last active admin fails with `last_admin` (exit 1). This is the break-glass path |
| `telescreen notify test --channel email\|slack --to REF [--template NAME]` | v1.2. Queue one notification through the real DLP-gated sender with the synthetic preview context and deliver it inline. Prints one JSON line per row (`notification_id`, `channel`, `status`, `dlp_verdict`, `error_code`). Exit 0 when all rows are `sent`, 1 otherwise, 2 for an unknown template, 3 for a configuration error |
| `telescreen search reindex` | Rebuild all search indexes through the IndexGuard |
| `telescreen mcp` | Run the Assistant MCP server over stdio |

Bare `telescreen` prints help and exits 2.

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELESCREEN_DATABASE_URL` | yes (except `workflow validate`) | - | SQLAlchemy async URL (`postgresql+asyncpg://...`) |
| `TELESCREEN_FINGERPRINT_KEY` | yes, unless `TELESCREEN_FINGERPRINT_KEYS` is set (except `workflow validate`) | - | Secret ref; HMAC key for fingerprints and resource keys (>= 32 bytes). From v1.1 it means key version 1; setting it together with `TELESCREEN_FINGERPRINT_KEYS` is a configuration error |
| `TELESCREEN_FINGERPRINT_KEYS` | no (v1.1) | unset | Comma list `<version>=<secret ref>`; version 1 is required (identity key for resource, placement and owner keys) |
| `TELESCREEN_FINGERPRINT_ACTIVE_VERSION` | no (v1.1) | highest configured version | Key version for new value fingerprints and external re-keying |
| `TELESCREEN_INTERNAL_DOMAINS` | no (v1.1) | unset | Comma list of internal domains for Drive exposure; unset makes user/group shares `unknown` |
| `TELESCREEN_OWNER_ROUTING` / `TELESCREEN_DEFAULT_TEAM` | no (v1.1) | `false` / unset | Owner routing when no policy matched; fallback team slug |
| `TELESCREEN_DIGEST_CRON` | no (v1.1) | `0 9 * * 1` | Weekly owner digest (UTC); empty disables |
| `TELESCREEN_DATA_OVERDUE_CRON` | no (v1.4) | `0 9 * * *` | Data retention overdue check and owner notification (UTC); empty disables |
| `TELESCREEN_HEALTH_RECIPIENTS` / `TELESCREEN_HEALTH_FAILURE_THRESHOLD` / `TELESCREEN_ALERT_UNSCANNABLE_PUBLIC` | no (v1.1) | unset / 3 / `true` | Health alert recipient refs (unset: state only, no alerts); consecutive failed scan jobs before alerting; alert on public unscannable resources |
| `TELESCREEN_RISK_WEIGHTS` | no (v1.1) | built-in defaults | Comma list `<group>.<name>=<value>` (0 < value <= 1) overriding risk weights; unknown keys are a configuration error |
| `TELESCREEN_POLICE_SIGNING_KEY` | for push police | - | Secret ref; master key deriving push signing secrets |
| `TELESCREEN_POLICE_ENROLLMENT_TOKEN` | for police registration | - | Secret ref |
| `TELESCREEN_POLICE_ALLOW_HTTP` | no | `false` | Allow http push endpoints (dev only) |
| `TELESCREEN_MEILI_URL` | no | unset (in-memory search) | Meilisearch base URL |
| `TELESCREEN_MEILI_API_KEY` | no | - | Secret ref |
| `TELESCREEN_DETECTORS` | no | `rule` | Detector list |
| `TELESCREEN_DETECTOR_MODE` | no | `union` | `union` or `cascade` |
| `TELESCREEN_DLP_DETECTORS` | no | `rule` | DLP gate detector list |
| `TELESCREEN_DLP_POLICY` | no | `mask` | `mask` or `block` |
| `TELESCREEN_MANAGED_LLM_PROVIDER` | no | `claude` | Managed LLM provider: `claude` (Anthropic API) or `codex` (OpenAI API); `design-docs/user-qa/qa-llm-data-egress.md` |
| `TELESCREEN_MANAGED_LLM_MODEL` | for `codex` | `claude-sonnet-5-5` when provider is `claude`; no default for `codex` | Managed LLM model id. Provider `codex` without this variable is a configuration error (exit 3) |
| `TELESCREEN_ANTHROPIC_API_KEY` | for provider `claude` | - | Secret ref |
| `TELESCREEN_OPENAI_API_KEY` | for provider `codex` | - | Secret ref |
| `TELESCREEN_OPENAI_BASE_URL` | no | `https://api.openai.com/v1` | OpenAI API base URL for provider `codex` |
| `TELESCREEN_LOCAL_LLM_BASE_URL` / `TELESCREEN_LOCAL_LLM_MODEL` | for local LLM | - | OpenAI-compatible server |
| `TELESCREEN_LOCAL_LLM_API_KEY` | no | - | Secret ref |
| `TELESCREEN_ML_MODEL` | for `ml` | - | Token classification model path or id |
| `TELESCREEN_LLM_BATCH_MAX_CHARS` / `_MAX_ITEMS` / `TELESCREEN_LLM_CONCURRENCY` | no | 12000 / 20 / 4 | LLM batching |
| `TELESCREEN_SCAN_BATCH_ITEMS` / `TELESCREEN_SCAN_MAX_BYTES` | no | 200 / 5242880 | Scan batching and resource size limit |
| `TELESCREEN_EVIDENCE_DIR` / `TELESCREEN_EVIDENCE_MAX_BYTES` | no | `./var/evidence` / 10485760 | Evidence storage. Image evidence is scanned by the managed LLM; with no managed LLM credentials, image uploads fail closed (503) |
| `TELESCREEN_SLACK_SIGNING_SECRET` / `TELESCREEN_SLACK_BOT_TOKEN` | for Slack | - | Secret refs |
| `TELESCREEN_GITHUB_WEBHOOK_SECRET` | for GitHub events | - | Secret ref (per-collector `credential_ref` holds API tokens) |
| `TELESCREEN_GITHUB_API_URL` | no | `https://api.github.com` | GitHub Enterprise base URL |
| `TELESCREEN_GOOGLE_SERVICE_ACCOUNT` | for Google | - | Secret ref to service account JSON |
| `TELESCREEN_GOOGLE_DELEGATED_ADMIN` | for domain-wide Drive sweeps | unset | Workspace admin email impersonated (domain-wide delegation) to list users for whole-domain sweeps; unset limits sweeps to the selector's drives/folders |
| `TELESCREEN_MAP_DISPATCH_SECONDS` / `TELESCREEN_SWEEP_SHARD_SIZE` | no | 2 / 100 | Map-reduce dispatch tick; Drive file ids per shard |
| `TELESCREEN_PUBLIC_BASE_URL` | for Drive watch and push callbacks | - | External Bureau URL |
| `TELESCREEN_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` / `_FROM` / `_STARTTLS` | for email | - / 587 / - / secret ref / - / `true` | SMTP settings |
| `TELESCREEN_WORKER_CONCURRENCY` / `TELESCREEN_WORKER_POLL_SECONDS` | no | 8 / 1.0 | Engine worker |
| `TELESCREEN_WEB_DIST` | no | unset (API only) | Built UI directory (`vite build` output, must contain `index.html`, else exit 3) served at `/` with SPA fallback for non-`/api/` paths; see `design-ui-sentry.md` section 8.6 |
| `TELESCREEN_LOG_LEVEL` | no | `INFO` | Log level |
| `TELESCREEN_TEST_DATABASE_URL` / `TELESCREEN_TEST_MEILI_URL` | tests only | unset | Enable `integration` tests |
| `TELESCREEN_OIDC_ISSUER` | no (v1.2) | unset (OIDC off) | Issuer URL. Enables OIDC access-token validation and SSO in the UI |
| `TELESCREEN_OIDC_CLIENT_ID` / `TELESCREEN_OIDC_AUDIENCE` / `TELESCREEN_OIDC_SCOPES` | no (v1.2) | `telescreen-ui` / client id / `openid profile email` | Public PKCE client, required `aud` member, scopes the UI requests |
| `TELESCREEN_OIDC_ALGORITHMS` / `TELESCREEN_OIDC_LEEWAY_SECONDS` / `TELESCREEN_OIDC_JWKS_CACHE_SECONDS` | no (v1.2) | `RS256` / 60 / 300 | Allowed asymmetric algorithms (`none` and `HS*` are a configuration error); clock leeway (0-300); JWKS cache lifetime |
| `TELESCREEN_OIDC_ROLE_CLAIM` / `TELESCREEN_OIDC_ROLE_MAP` / `TELESCREEN_OIDC_DEFAULT_ROLE` | no (v1.2) | `realm_access.roles` / `telescreen-admin=admin,telescreen-responder=responder,telescreen-viewer=viewer` / unset | Role claim path, claim-value-to-role map (highest wins), role for subjects with no mapped role (unset: 403) |
| `TELESCREEN_OIDC_GROUP_CLAIM` / `TELESCREEN_OIDC_TEAM_MAP` | no (v1.2) | `groups` / empty | Group claim path and `<group>=<team slug>` map; empty disables team sync |
| `TELESCREEN_OIDC_PROVISIONING` | no (v1.2) | `link` | `link` (link by verified email to an existing member) or `create` (JIT member creation) |
| `TELESCREEN_UI_TOKEN_LOGIN` | no (v1.2) | `true` | Offer static API-token login on the UI login page |
| `TELESCREEN_TEST_MAILPIT_URL` + `TELESCREEN_TEST_SMTP_HOST` / `_PORT`; `TELESCREEN_TEST_KEYCLOAK_URL`; `TELESCREEN_TEST_SLACK_BOT_TOKEN` + `TELESCREEN_TEST_SLACK_CHANNEL` | tests only (v1.2) | unset | Enable the matching `live` tests in `tests/live` |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Runtime error (masked message printed) |
| 2 | Usage error or validation failure (`workflow validate`) |
| 3 | Configuration error (missing/invalid setting, unresolvable secret ref, unavailable detector) |

### Dependencies (added once by the foundation plan)

| Group | Packages |
|-------|----------|
| runtime | fastapi, uvicorn, pydantic (v2), sqlalchemy[asyncio] (2.x), asyncpg, alembic, httpx, anthropic, mcp, pyyaml, croniter, pyjwt[crypto], jsonschema |
| optional `ml` | transformers, torch |
| dev | pytest, pytest-cov, pytest-asyncio, aiosqlite, ruff, ty, types-pyyaml, types-croniter, types-jsonschema |
| web (`src/web/package.json`) | solid-js, @solidjs/router, lucide-solid, yaml (both added in run B by p30); dev: typescript, vite, vite-plugin-solid, vitest, jsdom, @solidjs/testing-library, @testing-library/jest-dom, playwright-core (run B, screenshot smoke only) |

Decisions recorded with this table:

- The `codex` provider uses the OpenAI HTTP API through httpx (shared with the
  OpenAI-compatible local LLM client); there is no `openai` SDK dependency.
- Image metadata stripping for evidence uses a stdlib-only stripper
  (`src/telescreen/evidence/strip.py`), with no Pillow dependency. It walks container
  structure without decoding pixels and removes: JPEG APP1-APP15 and COM segments (APP0/JFIF
  kept); PNG `tEXt`, `iTXt`, `zTXt`, `eXIf` and `tIME` chunks; WebP `EXIF` and `XMP ` RIFF
  chunks, clearing the matching VP8X flags and fixing the RIFF size. Malformed containers
  are rejected (`evidence_rejected`, reason `malformed_image`).

Packaging: `[tool.hatch.build.targets.wheel] packages = ["src/telescreen",
"src/telescreen_police_sdk"]`; JSON schema files inside the packages are included.
pytest: `asyncio_mode = "auto"`, marker `integration`, `pythonpath = ["tools", "."]`.
mise tasks added: `web-install`, `web-typecheck`, `web-test`, `web-build`, `web-ci`.

---
