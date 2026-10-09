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
| `telescreen token create --label TEXT --scopes read,write` | Create an API token and print it once |
| `telescreen search reindex` | Rebuild all search indexes through the IndexGuard |
| `telescreen mcp` | Run the Assistant MCP server over stdio |

Bare `telescreen` prints help and exits 2.

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELESCREEN_DATABASE_URL` | yes (except `workflow validate`) | - | SQLAlchemy async URL (`postgresql+asyncpg://...`) |
| `TELESCREEN_FINGERPRINT_KEY` | yes (except `workflow validate`) | - | Secret ref; HMAC key for fingerprints and resource keys (>= 32 bytes) |
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
