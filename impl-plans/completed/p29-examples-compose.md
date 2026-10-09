# P29 Compose Deployment and Example Workflows Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/architecture.md sections 3 (Deployment and examples, including the run B4 non-root evidence-dir and example-police fingerprint-key constraints) and 8 (run B README exception, Run B2), design-docs/specs/command.md (Subcommands, Environment Variables), design-docs/specs/design-collectors-and-ingestion.md (Assembly bindings), design-docs/specs/design-workflow-engine.md (Document schema, escalation policy), design-docs/specs/design-ui-sentry.md sections 8.5, 8.6
**Created**: 2026-10-08
**Last Updated**: 2026-10-10 (run B4)

## Plan Metadata

- planId: `p29-examples-compose`
- planPath: `impl-plans/active/p29-examples-compose.md`
- wave: B4-2 (last; runs alone; earlier: B3-3 in s345, B2-3 in s342)
- dependsOn: `p28b-e2e-flows`, `p28c-leak-matrix`, plus the 30 integration-accepted plans at e24a433 (including `p28-assembly`, `p13-ui-workflows`, `p06-workflow-dsl`, `p08-masking`)
- writePaths:
  - `impl-plans/active/p29-examples-compose.md`
  - `examples/compose`
  - `examples/workflows`
  - `tests/examples`
  - `README.md`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Run B Intent

Run A delivered and accepted the compose stack, Dockerfile, `.env.example`, compose
README, the workflow examples and `tests/examples`. Run B finishes the product: every
other plan has joined, so this plan (1) reconciles `examples/compose` with the real CLI
and UI serving from p28/p30 instead of recreating it, (2) updates the root `README.md`
(run B ownership exception, architecture.md section 8), and (3) runs the final full gates
including the UI screenshot smoke.

Run B non-goals: no Python package or `src/web` edits (log Reconciliation requests for
defects found there); no new services; no docker run in tests; no changes to
`examples/police/` (accepted p11/p19 output).

## Run B Deliverables

- Compose reconciliation (fresh-read each file; change only what is wrong):
  - `bureau` runs `telescreen serve --host 0.0.0.0 --port 8000` with
    `TELESCREEN_WEB_DIST=/app/web` and no `VITE_TELESCREEN_MOCK` anywhere;
  - `Dockerfile` web stage runs `bun install --frozen-lockfile` then `bun run build`
    (the default `dist/`, which includes `public/fonts`) and copies `dist/` to
    `/app/web`; the CLI subcommands used by `migrate`, `engine` and the README exist in
    `src/telescreen/cli.py` (`telescreen --help` output);
  - `examples/compose/README.md`: "open http://localhost:8000" serves the UI; enter the
    admin token on Settings.
- `tests/examples/test_examples.py` additions: bureau service environment contains
  `TELESCREEN_WEB_DIST=/app/web`; the Dockerfile contains `bun install --frozen-lockfile`
  and copies the web build to `/app/web`; no file under `examples/compose` contains
  `VITE_TELESCREEN_MOCK`; every `telescreen <subcommand>` named in compose files exists in
  `telescreen.cli` (parse `main(["--help"])` output or the argparse parser).
- `README.md` (root; keep its sections and tone, edit in place):
  - Web application: `mise run web-ci`; development `cd src/web && bun run dev` (proxy to
    `:8000`); mock mode `VITE_TELESCREEN_MOCK=1 bun run dev` (masked fixtures, no
    backend); screenshot smoke `bun run screenshots` (needs Chrome/Chromium or
    `TELESCREEN_SCREENSHOT_BROWSER`; output `tmp/ui-screenshots/`); design reference
    `design-docs/specs/design-ui-sentry.md`; fonts are OFL (see `src/web/public/fonts`),
    icons Lucide (ISC).
  - Serving: `cd src/web && bun run build`, then
    `TELESCREEN_WEB_DIST=src/web/dist telescreen serve`; missing `index.html` exits 3;
    the wheel does not contain UI assets.
  - Local compose environment: fix the stale path (`examples/compose/docker-compose.yml`,
    not `compose.yaml`) and point to `examples/compose/README.md`.
  - Remove the stale sentence saying router mounting "belongs to the assembly plan".
  - CLI section matches `design-docs/specs/command.md` subcommands.
  - Collector webhooks (one or two sentences, design-collectors-and-ingestion.md
    "Assembly bindings"):
    - Slack and GitHub webhooks are rejected with 401 until
      `TELESCREEN_SLACK_SIGNING_SECRET` / `TELESCREEN_GITHUB_WEBHOOK_SECRET` are set as
      secret refs. In compose, the referenced secret files must also be created and
      mounted.
    - Drive channel tokens are derived from the fingerprint key; there is no extra
      variable.
  - Testing section: name `tests/e2e` (end-to-end flows) and `tests/leak` (raw-value
    leak matrix over every sink).

## Run B2 notes (s342)

- p28 was split into `p28-assembly` (container, collector bindings, CLI, serve),
  `p28b-e2e-flows` and `p28c-leak-matrix`. All three must be complete before this plan
  starts. If a final gate fails in their files, do not edit them. Write a Reconciliation
  request naming the owning plan and file.
- `examples/compose/.env.example`:
  - must not gain a Drive channel variable;
  - keeps `TELESCREEN_SLACK_SIGNING_SECRET` and `TELESCREEN_GITHUB_WEBHOOK_SECRET` UNSET
    on active lines. Unset is the documented fail-closed default: webhooks answer 401.
  - You may add them as commented example lines only, for example
    `# TELESCREEN_GITHUB_WEBHOOK_SECRET=file:/run/secrets/github_webhook_secret`.
- Pitfall (exit 3):
  - Never add an active (uncommented) `file:` ref unless all three hold:
    - `docker-compose.yml` declares that secret under top-level `secrets:`;
    - every service using `env_file: .env` mounts it;
    - `examples/compose/README.md` creates the file.
  - A set but unresolvable ref raises `ConfigError` at startup (`core/secrets.py:resolve_secret`,
    design "Assembly bindings"). Bureau and engine would exit 3, and the example would no
    longer be runnable.
- `tests/examples/test_examples.py`: add a consistency test.
  - For every active (not `#`-commented) `.env.example` line whose value starts with
    `file:/run/secrets/<name>`, `<name>` is a key of the top-level `secrets` mapping in
    `docker-compose.yml`.
  - Parse with the existing `parse_yaml` helper. Do not weaken the existing
    ref/placeholder rule.
- README (root and `examples/compose/README.md`): state that Slack and GitHub webhooks
  return 401 until the operator sets these refs AND creates and mounts the matching
  secret files.
- `tests/examples/test_examples.py`: add one test.
  - It runs `telescreen.cli.main(["workflow", "validate", <path>])` for each of
    `github-credential-leak.yaml`, `slack-pii.yaml`, `gdrive-mynumber-sheet.yaml` and
    `drive-weekly-sweep.yaml`.
  - Each call must return 0.
  - `escalation-policies.json` is not a workflow; keep its existing loader test.
- If `main(["--help"])` is parsed for subcommand names, it raises `SystemExit`. Catch it
  with `pytest.raises(SystemExit)` and read `capsys`.

## Run B4 additions (architecture.md section 3, accepted design)

State at e24a433 (checked by the plan author):
- none of the run B deliverables above are in the tree yet;
- in root `README.md`, the stale `compose.yaml` path and the "belongs to the assembly
  plan" sentence are still present;
- the README has no UI dev/mock/screenshots/serve text, no webhook 401 note, and does not
  mention `tests/e2e` or `tests/leak`;
- `docker-compose.yml` has no explicit `TELESCREEN_WEB_DIST` on `bureau` (the Dockerfile
  sets it as an `ENV`).

Implement them all, plus these two items.

- Writable evidence directory (non-root image):
  - In `Dockerfile`, after `useradd` and before `USER telescreen`, create
    `/app/var/evidence` and `chown` it (and `/app/var`) to `telescreen`.
  - In `docker-compose.yml`, add a top-level named volume `evidence-data`. Mount it as
    `evidence-data:/app/var/evidence` on `bureau` and `engine`.
  - `.env.example` keeps `TELESCREEN_EVIDENCE_DIR=/app/var/evidence`.
  - Why: `src/telescreen/evidence/store.py:LocalFileEvidenceStore.put` runs `mkdir` lazily
    as uid 10001, and `/app` is owned by root. A named volume copies the image directory's
    ownership on first use.
  - Do not mount a bind path, and do not run as root.
- Example Police fingerprint key:
  - `examples/police/python/private_db_police.py:main` reads `TELESCREEN_FINGERPRINT_KEY`
    as raw key bytes. Today the service inherits the Bureau's
    `file:/run/secrets/fingerprint_key` ref string from `.env`.
  - In the `example-police` `environment`, add
    `TELESCREEN_FINGERPRINT_KEY: ${POLICE_FINGERPRINT_KEY}`. Compose `environment`
    overrides `env_file`.
  - In `.env.example`, add `POLICE_FINGERPRINT_KEY=replace-me-placeholder` (it passes the
    existing placeholder rule).
  - In `examples/compose/README.md`, create `secrets/police_fingerprint_key` with a
    placeholder `printf`, then
    `export POLICE_FINGERPRINT_KEY="$(cat secrets/police_fingerprint_key)"`. This is the
    same pattern as `POLICE_ENROLLMENT_TOKEN`.
  - Do NOT add it to the compose top-level `secrets:` list, and do NOT reuse the Bureau
    key: this is the Police's own key.
  - Do NOT edit `examples/police/**` (accepted p11/p19 output; not in writePaths).
- New `tests/examples/test_examples.py` tests (same style as
  `test_compose_stack_parses_with_six_services`; `parse_yaml` and `JsonValue` accessors
  only; no dict variables). Each entry is situation -> expected outcome:
  - `bureau` and `engine` `volumes` -> each has an item ending in `:/app/var/evidence`,
    and the top-level `volumes` names include `evidence-data`;
  - `Dockerfile` text -> the line containing `/app/var/evidence` and `chown` comes after
    the `useradd` line and before `USER telescreen`;
  - `example-police` `environment.TELESCREEN_FINGERPRINT_KEY` -> equals
    `${POLICE_FINGERPRINT_KEY}`;
  - `bureau` `environment.TELESCREEN_WEB_DIST` -> equals `/app/web` (run B deliverable).
  - Also add the run B tests listed above: the `bun install --frozen-lockfile` and
    `/app/web` Dockerfile checks; no `VITE_TELESCREEN_MOCK` under `examples/compose`;
    CLI subcommands exist; the `.env.example` `file:/run/secrets/<name>` consistency
    check; `workflow validate` exits 0 for all 4 workflow examples.
- Invariants:
  - the six services are unchanged;
  - webhook secrets stay unset on active `.env.example` lines;
  - every `*_KEY`, `*_TOKEN`, `*_SECRET` and `*_PASSWORD` value is a ref or a placeholder;
  - `find_leaks` is empty for every example file;
  - no file outside writePaths changes.
- Full-suite pitfall:
  - `tests/workflow/mapreduce/test_mapreduce.py::test_dispatch_caps_ten_thousand_shards_at_thirty_two`
    takes about 40 s and is load-sensitive.
  - If the whole-repository pytest fails ONLY on it, rerun it alone. Then rerun the full
    suite with no other heavy process.
  - Only the final passing full run is a verification entry. Earlier attempts go in the
    Progress Log (conventions 7a).
  - Never edit map-reduce code or that test.

## Run B Verification (final gates; all must pass)

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache . 2>&1 | tee /tmp/telescreen-p29-final-format.log; echo "exit=$?"
uv run --frozen ruff check --no-cache . 2>&1 | tee /tmp/telescreen-p29-final-ruff.log; echo "exit=$?"
uv run --frozen python tools/typelint.py 2>&1 | tee /tmp/telescreen-p29-final-typelint.log; echo "exit=$?"
uv run --frozen ty check 2>&1 | tee /tmp/telescreen-p29-final-ty.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider 2>&1 | tee /tmp/telescreen-p29-final-pytest.log; echo "exit=$?"
uv build --out-dir /tmp/telescreen-p29-final-dist 2>&1 | tee /tmp/telescreen-p29-final-build.log; echo "exit=$?"
(cd src/web && (test -d node_modules || bun install --frozen-lockfile) && bun run typecheck && bun run test && bun run build --outDir /tmp/telescreen-p29-final-web --emptyOutDir) 2>&1 | tee /tmp/telescreen-p29-final-web.log; echo "exit=$?"
(cd src/web && bun run screenshots) 2>&1 | tee /tmp/telescreen-p29-screenshots.log; echo "exit=$?"
ls tmp/ui-screenshots/*.png | wc -l    # 27 (13 pages x 2 themes + mobile issues light)
uv run --frozen telescreen workflow validate examples/workflows/github-credential-leak.yaml examples/workflows/slack-pii.yaml examples/workflows/gdrive-mynumber-sheet.yaml examples/workflows/drive-weekly-sweep.yaml 2>&1 | tee /tmp/telescreen-p29-workflow-validate.log; echo "exit=$?"
test -z "$(git status --porcelain -- design-docs)"; echo "exit=$?"   # design-docs untouched (the run B4 architecture.md section 3 edit is committed in the run checkpoint before dispatch; empty output expected)
uv run --frozen pytest -p no:cacheprovider tests/examples 2>&1 | tee /tmp/telescreen-p29-final-examples.log; echo "exit=$?"   # includes the run B and run B4 tests
command -v docker && docker compose -f examples/compose/docker-compose.yml --env-file examples/compose/.env.example config > /tmp/telescreen-p29-compose-config.log 2>&1; echo "exit=$?"
```

The screenshot run exits 2 when no browser exists: record that as a verification gap
(not a pass) in the Progress Log and in the step result `verificationGaps`. When docker
is not installed, the `docker compose config` check is a verification gap (static
`tests/examples` checks still apply). Look at the light and dark screenshots of Issues,
issue detail and Run detail and note in the Progress Log that tokens, rail, panel and
TopBar render (no unstyled page, no raw-looking value).

## Run B Completion Criteria

- [x] Compose files reconciled; new `tests/examples` assertions pass (including `workflow validate` exit 0 for all four workflow examples and the `.env.example` active `file:/run/secrets/<name>` -> compose `secrets.<name>` consistency check); webhook secrets stay unset on active `.env.example` lines
- [x] Root README documents setup, architecture, UI (dev, mock, screenshots), serve and compose
- [x] Run B4 additions delivered: evidence-data volume on bureau and engine with a non-root-owned `/app/var/evidence`; example-police `TELESCREEN_FINGERPRINT_KEY: ${POLICE_FINGERPRINT_KEY}` with the placeholder and README steps; their tests pass
- [x] All final gates above pass (or the browser/docker gaps are recorded explicitly)
- [x] Progress Log updated (Run B4 session: hashes before and after, final passing commands only)

## Run A Intent and Deliverables (accepted history)

## Intent and Context

Acceptance: "examples/compose and examples/workflows provide runnable deployment and
example workflow definitions (GitHub credential leak multi-team remediation with
escalation and police re-verification)". The CLI command names are pinned in
`command.md` (implemented by p28). The DSL loader (p06) validates every example offline.

## Non-goals

- No Python package code. No edits to `examples/police/` (owned by p11). (Run A only:
  no `README.md` edits; run B updates it as listed above.)
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
uv run --frozen python tools/typelint.py tests/examples > /tmp/telescreen-p29-examples-compose-typelint-scoped.log 2>&1
uv run --frozen python tools/typelint.py > /tmp/telescreen-p29-examples-compose-typelint.log 2>&1; ! grep -E '(^|/)tests/examples/' /tmp/telescreen-p29-examples-compose-typelint.log
```

## Completion Criteria

- [x] Compose stack, Dockerfile, `.env.example` and README delivered
- [x] Four workflow examples plus the escalation policies delivered and validated
- [x] No secrets (tests pass)
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 07:33 JST
**Tasks Completed**: Added the six-service Compose stack, multi-stage non-root Dockerfile, secret-reference environment template and setup guide; added four validated workflows, two escalation policies, and tests for DSL loading, triggers, redaction, leak samples, Compose services and placeholder/ref secret settings.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: None
**Hashes**: `examples/compose/docker-compose.yml` before=absent after=635d68915da39f4a5ce4d3b02c789270acfb46b2c43e370ec2a2441bcc44d0fb; `examples/compose/Dockerfile` before=absent after=7001e2c75b9050dcd14f441d7834b964cf9cc6b90059ad6b033d93c2740fb532; `examples/compose/.env.example` before=absent after=c784b59794e1bcceacf5c35c11deb2f252170d4c282c36aaaf8123fa8e3d65bf; `examples/compose/README.md` before=absent after=3f1a7d41e47b1b91e6eaebb09dd95ba83c7c8d3cfb0558e53ecf70897bd6740f; `examples/workflows/github-credential-leak.yaml` before=absent after=628755ef76abe3f46f0a9e082c225ed835aaee1bf0f16dede01d283f9e849318; `examples/workflows/slack-pii.yaml` before=absent after=ee513f72c68527f84793e67c67e9d5633334d5095cab295a8b8fff9de68894ce; `examples/workflows/gdrive-mynumber-sheet.yaml` before=absent after=81ee0eb49b1fed2842f348a0ec0241ec6581282ec9bc2794fb6667d62be30058; `examples/workflows/drive-weekly-sweep.yaml` before=absent after=2fa0d90afb64b5e92114919855015e9ced6adbd425783fbf92bfc8fd6cd1001e; `examples/workflows/escalation-policies.json` before=absent after=a0ab1491ac154527b804cd25580fd34438607325b94aaf0fab5f9343a0c60d87; `tests/examples/test_examples.py` before=absent after=13e644da10ea7d32945e2c20c17657fefa37466121c60cad1674c9721f16aa44; this plan before=f6cabe177f3154416b7825c1079bcb339552a4e9eb75e942225f54ebdeb76ebf.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache tests/examples` -> exit 0 (log: `/tmp/telescreen-p29-examples-compose-format-final3.log`); `uv run --frozen ruff check --no-cache tests/examples` -> exit 0 (log: `/tmp/telescreen-p29-examples-compose-ruff-final3.log`); `uv run --frozen ty check tests/examples` -> exit 0 (log: `/tmp/telescreen-p29-examples-compose-ty-final3.log`); `uv run --frozen pytest -p no:cacheprovider tests/examples` -> exit 0, 6 passed (log: `/tmp/telescreen-p29-examples-compose-pytest-final3.log`); `uv run --frozen python tools/typelint.py tests/examples` -> exit 0, 1 file clean (log: `/tmp/telescreen-p29-examples-compose-typelint-scoped-final3.log`); full-tree `uv run --frozen python tools/typelint.py` -> exit 1 with repository diagnostics outside `tests/examples`; unanchored p29 filter -> grep exit 1 (no p29 diagnostics; full log: `/tmp/telescreen-p29-examples-compose-typelint-final3.log`, filter log: `/tmp/telescreen-p29-examples-compose-typelint-scope-final3.log`).
**Notes**: The full-tree typelint result is an aggregate failure from other plan paths, not a p29 pass; the p29-scoped command passes. Docker execution was not run, as required by this plan. The Dockerfile copies `examples/police/`, which is owned by p11 and is not present until that plan lands. Formal test-integrity, adversarial, and combined-tree review remain downstream workflow steps.

### Session: 2026-10-10 Run B4
**Tasks Completed**: Reconciled the six-service Compose stack with the assembled CLI/UI; added `TELESCREEN_WEB_DIST`, non-root evidence directory ownership and named evidence volume, and the example Police's independent fingerprint key; added Compose/workflow conformance tests; updated root and Compose READMEs; ran final Python, package, web, examples, workflow and Compose configuration gates.
**Tasks In Progress**: None in the p29 implementation scope.
**Blockers**: None.
**Reconciliation requests**: `p30-ui-design-system`, `src/web/scripts/screenshots.ts`: the script starts `vite preview` at `localhost:4319` and probes `127.0.0.1:4319`. On this environment the preview is reachable at `localhost` (HTTP 200) while IPv4 `127.0.0.1` is refused. `bun run screenshots` exits 1 after its build because its readiness probe never succeeds. The script is outside p29 writePaths; align the bind host and probe address, then rerun screenshot capture and visual inspection.
**Hashes**: `README.md` before=a2563325c06941bc97db35137463c380b0768eec3c3b6d4a159a4c5efddccc68 after=3c5b4c672333b4d498e3141dafadcabf432a1e812300ed94530530b6c8a60825; `examples/compose/docker-compose.yml` before=635d68915da39f4a5ce4d3b02c789270acfb46b2c43e370ec2a2441bcc44d0fb after=b18c5230f6d5dcd1d0ecef8f8486235c2c9781b8220b6b79bbfcec6ca8575a75; `examples/compose/Dockerfile` before=7001e2c75b9050dcd14f441d7834b964cf9cc6b90059ad6b033d93c2740fb532 after=bae89b4bc6e75adbd373cfcd55630f88c374ac3fa7bdc12c5649d1c81977f28d; `examples/compose/.env.example` before=c784b59794e1bcceacf5c35c11deb2f252170d4c282c36aaaf8123fa8e3d65bf after=93df8d3480ac2f6633cfb8c815b63de007f5e5d5a8c9b41a5f1a866d50eff10b; `examples/compose/README.md` before=3f1a7d41e47b1b91e6eaebb09dd95ba83c7c8d3cfb0558e53ecf70897bd6740f after=1d313c207a3724934bfdda1d0af8f5d2e6d70afba9c3e2578ee8e36a8074a27d; `tests/examples/test_examples.py` before=13e644da10ea7d32945e2c20c17657fefa37466121c60cad1674c9721f16aa44 after=660e60262ecdc4d8bd92a757f5d7b522f46c216a7543b7e57ec7291c0cf1cdc0; this plan before=2e33ad9d7c63b23f9e790c0b0207118764df200c6ec431e207834d13347eba0d after=reported in the Step 6 payload to avoid embedding a self-referential digest.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache . 2>&1 | tee /tmp/telescreen-p29-final-format.log` -> exit 0 (291 files already formatted).
- `set -o pipefail; uv run --frozen ruff check --no-cache . 2>&1 | tee /tmp/telescreen-p29-final-ruff.log` -> exit 0.
- `set -o pipefail; uv run --frozen python tools/typelint.py 2>&1 | tee /tmp/telescreen-p29-final-typelint.log` -> exit 0 (290 files clean).
- `set -o pipefail; uv run --frozen ty check 2>&1 | tee /tmp/telescreen-p29-final-ty.log` -> exit 0.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider 2>&1 | tee /tmp/telescreen-p29-final-pytest.log` -> exit 0, 393 passed, 4 skipped, 0 failed.
- `set -o pipefail; uv build --out-dir /tmp/telescreen-p29-final-dist 2>&1 | tee /tmp/telescreen-p29-final-build.log` -> exit 0.
- `set -o pipefail; (cd src/web && (test -d node_modules || bun install --frozen-lockfile) && bun run typecheck && bun run test && bun run build --outDir /tmp/telescreen-p29-final-web --emptyOutDir) 2>&1 | tee /tmp/telescreen-p29-final-web.log` -> exit 0, 26 test files / 114 tests passed.
- `set -o pipefail; uv run --frozen telescreen workflow validate examples/workflows/github-credential-leak.yaml examples/workflows/slack-pii.yaml examples/workflows/gdrive-mynumber-sheet.yaml examples/workflows/drive-weekly-sweep.yaml 2>&1 | tee /tmp/telescreen-p29-workflow-validate.log` -> exit 0.
- `set -o pipefail; uv run --frozen pytest -p no:cacheprovider tests/examples 2>&1 | tee /tmp/telescreen-p29-final-examples.log` -> exit 0, 14 passed, 0 failed.
- `docker compose -f examples/compose/docker-compose.yml --env-file examples/compose/.env.example config` -> exit 0 (log: `/tmp/telescreen-p29-compose-config.log`; temporary placeholder `.env` and declared secret files were removed).
- `test -z "$(git status --porcelain -- design-docs)"` -> exit 0; `test "$(grep -c '^- \[ \]' impl-plans/active/p29-examples-compose.md)" -eq 0` -> exit 0 after this plan update.
**Notes**: Earlier attempts were superseded: the first focused pytest wrapper returned 1 because zsh reserves the variable name `status` although pytest passed 14 tests (`tmp/telescreen-v1-20261010-b4/p29-examples-compose/prior-examples-wrapper.log`); the first static pass exposed TL003 on a new unpacking and the typed binding was corrected (`tmp/telescreen-v1-20261010-b4/p29-examples-compose/prior-static-gates.log`); the first Compose config lacked the required local `.env`, then passed with temporary placeholder inputs (`tmp/telescreen-v1-20261010-b4/p29-examples-compose/prior-compose-config.log`). Screenshot command attempts and the direct loopback probe are verification-gap evidence, not passing gates: `bun run screenshots` exited 1 (complete log `/tmp/telescreen-p29-screenshots-rerun.log`), the preview build succeeded, `curl http://localhost:4319/` returned 200, and `curl http://127.0.0.1:4319/` was refused. No UI screenshots were produced or visually inspected. No `design-docs` files changed. Formal test-integrity, adversarial and integration review, and subsequent workflow finalization remain downstream.

**Owner resolution (2026-10-10)**: The screenshot smoke gap is fixed by the owner outside p29's
write paths: `src/web/scripts/screenshots.ts` now starts `vite preview` with `--host 127.0.0.1`
(the readiness probe and page URLs use 127.0.0.1; localhost resolved to IPv6) and logs every
browser error and HTTP >= 400 response with its URL; `src/web/index.html` now has an inline SVG
favicon (the only browser error was the missing /favicon.ico 404). `cd src/web && bun run build
&& bun run screenshots` now exits 0 with zero browser errors (light, dark and mobile). Re-run
it and report it as a passing verification; this is not a gap. Report per conventions 7a.

### Session: 2026-10-10 Run B4 retry
**Tasks Completed**: Refreshed p29 evidence after the owner resolution above. The screenshot run exited 0 and generated 27 PNGs; inspected Issues, issue detail and Run detail in light and dark themes. Tokens, rail, secondary panel and TopBar render; the sample credential is masked and no raw-looking value appears. All final Python, web, example, workflow, Compose and plan checks passed.
**Tasks In Progress**: None in the p29 implementation scope.
**Blockers**: None.
**Reconciliation requests**: None. The screenshot runner mismatch is resolved by the owner changes referenced in the Owner resolution note; no p29 source change was needed.
**Hashes**: plan before retry=`5b53172b143a7137947265fb716711c0633966f31aaf1e76041358756a133dbf`; plan after appending retry content, before recording this hash=`4e54da635136497ab6d1bf0dea39210ce4cae7368db61ef229f01cb608f60c27` (final plan SHA-256 is in the Step 6 result). Owner files remain unchanged: `src/web/scripts/screenshots.ts`=`7b1cf55d06ee64d566971e7fd7623dff7121aff83caa507955fdacea137cd8e9`, `src/web/index.html`=`b50dbc3f256392eeaff597a5ee822516126cbeb9ef083e1afe579f3b47e3cd62`; p28b/p28c files remain unchanged: `impl-plans/active/p28b-e2e-flows.md`=`cf52c87eaabdc60a71003997ec51d24a560bdb49cff7a1e7e64dba5926c43b31`, `impl-plans/active/p28c-leak-matrix.md`=`db7ea54de7ba01e7c670291adc8047f599a7b3ab12a07fb9e03564b7c7f09723`, `tests/leak/test_leak_matrix.py`=`de970047ec73ff97591fb434da58b0444eda8d471afb643b67cbe03893dede99`.
**Verification**:
- `uv run --frozen ruff format --check --no-cache .` -> exit 0 (log: `/tmp/telescreen-p29-retry-format.log`; 291 files already formatted).
- `uv run --frozen ruff check --no-cache .` -> exit 0 (log: `/tmp/telescreen-p29-retry-ruff.log`).
- `uv run --frozen python tools/typelint.py` -> exit 0 (log: `/tmp/telescreen-p29-retry-typelint.log`; 290 files clean).
- `uv run --frozen ty check` -> exit 0 (log: `/tmp/telescreen-p29-retry-ty.log`).
- `uv run --frozen pytest -p no:cacheprovider` -> exit 0, 393 passed, 4 skipped (log: `/tmp/telescreen-p29-retry-pytest.log`; run alone).
- `uv build --out-dir /tmp/telescreen-p29-retry-dist` -> exit 0 (log: `/tmp/telescreen-p29-retry-build.log`).
- `cd src/web && (test -d node_modules || bun install --frozen-lockfile) && bun run typecheck && bun run test && bun run build --outDir /tmp/telescreen-p29-retry-web --emptyOutDir` -> exit 0, 26 test files / 114 tests passed (log: `/tmp/telescreen-p29-retry-web.log`).
- `cd src/web && bun run build && bun run screenshots` -> exit 0, zero browser errors, 27 PNGs (log: `/tmp/telescreen-p29-screenshots-final.log`; images under `tmp/ui-screenshots/`).
- `uv run --frozen telescreen workflow validate examples/workflows/github-credential-leak.yaml examples/workflows/slack-pii.yaml examples/workflows/gdrive-mynumber-sheet.yaml examples/workflows/drive-weekly-sweep.yaml` -> exit 0 (log: `/tmp/telescreen-p29-retry-workflow-validate.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/examples` -> exit 0, 14 passed (log: `/tmp/telescreen-p29-retry-examples.log`).
- `docker compose -f examples/compose/docker-compose.yml --env-file examples/compose/.env.example config` -> exit 0 with temporary placeholder `.env` and declared secret files; temporary inputs were removed (log: `/tmp/telescreen-p29-retry-compose-config.log`).
- `test -z "$(git status --porcelain -- design-docs)"` -> exit 0; `test "$(grep -c '^- \\[ \]' impl-plans/active/p29-examples-compose.md)" -eq 0` -> exit 0; `test "$(wc -l < impl-plans/active/p29-examples-compose.md)" -lt 400` -> exit 0.
**Notes**: The initial retry Compose check without the documented local `.env` exited 1; it was rerun with temporary placeholder inputs and passed. Earlier screenshot failure details remain in the original Run B4 Notes as historical evidence; the current screenshot verification above passes. Formal test-integrity, adversarial and integration reviews, shared documentation/index updates, and workflow finalization remain downstream.
