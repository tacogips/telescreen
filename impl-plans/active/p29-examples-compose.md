# P29 Compose Deployment and Example Workflows Implementation Plan

**Status**: Ready
**Design Reference**: design-docs/specs/architecture.md section 3 (Deployment and examples), design-docs/specs/command.md (Subcommands, Environment Variables), design-docs/specs/design-workflow-engine.md (Document schema, escalation policy)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p29-examples-compose`
- planPath: `impl-plans/active/p29-examples-compose.md`
- wave: 4
- dependsOn: `p06-workflow-dsl`, `p08-masking`
- writePaths:
  - `impl-plans/active/p29-examples-compose.md`
  - `examples/compose`
  - `examples/workflows`
  - `tests/examples`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Acceptance: "examples/compose and examples/workflows provide runnable deployment and
example workflow definitions (GitHub credential leak multi-team remediation with
escalation and police re-verification)". The CLI command names are pinned in
`command.md` (implemented by p28). The DSL loader (p06) validates every example offline.

## Non-goals

- No Python package code. No edits to `examples/police/` (owned by p11) or `README.md`
  (p01).
- Tests do not run docker.

## Deliverables

- `examples/compose/docker-compose.yml` with these services:
  - `postgres` (`postgres:16`, healthcheck `pg_isready`, volume);
  - `meilisearch` (`getmeili/meilisearch:v1.11`, pinned minor; master key from env);
  - `migrate` (one-shot `telescreen migrate`, depends on healthy postgres);
  - `bureau` (`telescreen serve --host 0.0.0.0 --port 8000`, `TELESCREEN_WEB_DIST=/app/web`,
    port 8000, depends on migrate completed);
  - `engine` (`telescreen worker`);
  - `example-police` (runs `python /app/examples/police/python/private_db_police.py`
    against `http://bureau:8000`).
  All services use `env_file: .env`.
- `examples/compose/Dockerfile`, multi-stage:
  1. `oven/bun` stage: copy `src/web`, `bun install --frozen-lockfile`, `bun run build`;
  2. `ghcr.io/astral-sh/uv` and python 3.12 stage: `uv build` the wheel;
  3. runtime `python:3.12-slim`: install the wheel, copy the built UI to `/app/web` and
     the police examples to `/app/examples/police`, run as a non-root user.
- `examples/compose/.env.example`: every variable the services need. Secrets only as
  `env:` or `file:` refs or obvious placeholders (`TELESCREEN_FINGERPRINT_KEY=file:/run/secrets/fingerprint_key`,
  `POSTGRES_PASSWORD=change-me-placeholder`). Hosts use `example.invalid`.
- `examples/compose/README.md`: quick start (`cp .env.example .env`, create the secret
  files, `docker compose up --build`), creating the first admin token
  (`docker compose run --rm bureau telescreen token create --label admin --scopes admin`),
  enrolling the example police, and opening the UI.
- `examples/workflows/github-credential-leak.yaml`, which must include:
  - trigger `issue_created` and `issue_regressed` matching source github and classes
    credential, token, private_key at min severity high;
  - a `detect` rescan;
  - an `ai_agent` verification with a managed model (output `is_live_credential: boolean`,
    `summary: string`);
  - a `condition` branch;
  - a first-line triage `human_task` assigned to `team:secops-triage` with SLA PT4H and
    escalation policy `secops-standard`;
  - a `notify` to the dev team;
  - a dev-team `evidence_upload` (history rewrite proof) with SLA and escalation;
  - a `human_task` for the GitHub Enterprise admin team to verify refs, forks and caches;
  - `police_verify` `verify_remediation` with labels `scope=github-enterprise` and a
    timeout of PT2H;
  - `send_back` to the dev step on police failure (`max_send_backs: 2`,
    `on_exhausted` escalation notify);
  - an `end` succeeded.
- `examples/workflows/slack-pii.yaml`: Slack PII triage with notify and a human task.
- `examples/workflows/gdrive-mynumber-sheet.yaml`: Drive spreadsheet with My Number:
  detect, triage, evidence and police verify.
- `examples/workflows/drive-weekly-sweep.yaml`: a manual-trigger workflow with a `map`
  step (`via: collector`, collector `gdrive-domain`, `max_concurrency: 64`) and outcomes
  that notify ops.
- `examples/workflows/escalation-policies.json`: `secops-standard` from the design, plus
  `dev-remediation`.
- `tests/examples/test_examples.py`:
  - every `.yaml` and `.json` workflow loads with `load_workflow` without errors;
  - the escalation policies load with `load_escalation_policy`;
  - the GitHub workflow contains each required step type (`detect`, `ai_agent`,
    `condition`, `human_task`, `notify`, `evidence_upload`, `police_verify`,
    `send_back`, `end`);
  - `docker-compose.yml` parses with `parse_yaml` and has the six services;
  - `find_leaks` is empty for every file under `examples/compose` and
    `examples/workflows`;
  - `.env.example` values are refs, placeholders or non-secret settings (every
    `*_KEY`, `*_TOKEN`, `*_SECRET` and `*_PASSWORD` value starts with `env:` or `file:`,
    or contains `placeholder`).

## Key Pitfalls

- Use placeholder domains (`example.invalid`) and never realistic tokens. The gitleaks
  pre-commit hook scans these files.
- The workflow files must not contain anything the Redactor flags (the upload API
  rejects such sources). Avoid email-like strings: use `team:` and `member:` refs and
  `email:secops-leads@example.invalid` only in escalation policies, which are not
  workflow sources. Add a test asserting
  `telescreen.masking.redact.Redactor.default().has_hits(text)` is False for every
  workflow file (p08 is a dependency).

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache tests/examples
uv run --frozen ruff check --no-cache tests/examples
uv run --frozen ty check tests/examples
uv run --frozen pytest -p no:cacheprovider tests/examples 2>&1 | tee /tmp/telescreen-p29-examples-compose-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p29-examples-compose-typelint.log 2>&1; grep -E '^tests/examples' /tmp/telescreen-p29-examples-compose-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] Compose stack, Dockerfile, `.env.example` and README delivered
- [ ] Four workflow examples plus the escalation policies delivered and validated
- [ ] No secrets (tests pass)
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
