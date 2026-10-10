# V11-P20 Docs and Examples: README v1.1 Features, Owner-Review Workflow Example, Compose Settings

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 1 (F1-F10), 7 (owner-review example), 15 (owner decisions); design-docs/specs/command.md (v1.1 environment variables)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p20-docs-examples`
- planPath: `impl-plans/active/v11-p20-docs-examples.md`
- wave: 4
- dependsOn: `v11-p13-workflow` (owner_review DSL), `v11-p17-api-issues`, `v11-p18-api-ops`
- writePaths:
  - `impl-plans/active/v11-p20-docs-examples.md`
  - `README.md`
  - `examples/workflows/owner-review.yaml` (new)
  - `examples/workflows/escalation-policies.json`
  - `examples/compose/.env.example`
  - `examples/compose/README.md`
  - `tests/examples/test_examples.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

The acceptance criteria require the README feature list and the examples to describe
v1.1. The README has no explicit feature section today: features are spread over the
Architecture (line ~9), Data handling (~23), API (~62) and Web application (~70)
sections. Add a concise "Features (v1.1)" subsection after Architecture, and update the
Data handling, API and Web application sections where v1.1 changes them.

## Non-goals

- No design document edits (the design step already updated them). No code changes.
- No secrets: example values are placeholders and secret references only.

## Invariants

- Every example workflow still loads (`tests/examples/test_examples.py` loads all
  `examples/workflows/*.yaml` and scans them for detected values).
- English only, no emojis.

## Deliverables

1. **README**:
   - A "Features (v1.1)" list covering F1-F10 in one line each: exposure-aware severity
     with `critical`, placement policies (versioned, allow/violate, `max_age_days`),
     verified resolution (`pending_verification`, rescan decides; policy changes are
     never remediation), scan coverage and the unscannable list, owner routing with
     `owner_review`, digest and My tasks, collector and Police health alerts, linked
     occurrences and fingerprint key versioning, risk score and escalation `min_risk`,
     exceptions with approval and expiry.
   - A sentence restating the owner decisions: no validity checks, no raw-value reveal
     (deep links only), read-only on every source.
   - Data handling: new records hold only enums, counts, keyed references and redacted
     text.
   - API: the v1.1 endpoint groups (pointer to `design-docs/specs/design-api-and-ui.md`).
   - Web application: the My tasks and Policies pages, and the 35-image screenshot smoke.
   - Configuration: the new `TELESCREEN_*` variables (pointer to `command.md`), including
     the key rotation procedure:
     1. add `2=<ref>` to `TELESCREEN_FINGERPRINT_KEYS`;
     2. set the active version to 2;
     3. keep version 1, which is the identity key.
2. **`examples/workflows/owner-review.yaml`**:
   - trigger `issue_created` with `min_severity: high`;
   - a `human_task` with `kind: owner_review`, `assignee: issue:assignee`, the 4
     outcomes routing to: `remove` / `restrict` -> a `detect` step (verification)
     followed by `end`; `exception` -> `end`; `not_mine` -> a notify step to a team
     placeholder (`team:security`) then `end`;
   - `sla: P2D` and `escalation_policy` referencing a policy in
     `escalation-policies.json`.
3. **`escalation-policies.json`**: one tier gains `"min_risk": 60`. It must still load.
4. **`examples/compose/.env.example`**:
   - commented placeholders for `TELESCREEN_INTERNAL_DOMAINS=example.com`,
     `TELESCREEN_OWNER_ROUTING=false`, `TELESCREEN_DEFAULT_TEAM=security`,
     `TELESCREEN_HEALTH_RECIPIENTS=team:security`, `TELESCREEN_DIGEST_CRON=0 9 * * 1`;
   - a commented `TELESCREEN_FINGERPRINT_KEYS` example using `file:` refs;
   - keep `TELESCREEN_FINGERPRINT_KEY` active (backward compatible).

   `examples/compose/README.md` gets a short v1.1 settings note.
5. **`tests/examples/test_examples.py`**:
   - `owner-review.yaml` loads, has a human_task with `kind == owner_review` and outcomes
     exactly the 4 names;
   - the escalation policy tier with `min_risk` loads;
   - `.env.example` contains no value matching the sample detectors (the existing
     scanning pattern).

## Verification

Conventions section 7 with `<tests>` = `tests/examples` (there is no Python source path;
run ruff and ty on `tests/examples` only). Also
`uv run --frozen python -m telescreen workflow validate examples/workflows/owner-review.yaml > /tmp/telescreen-v11-p20-docs-examples-validate.log 2>&1; echo "exit=$?"`
-> `exit=0`. Check the exact CLI form with `uv run --frozen python -m telescreen --help`
first, and record the command actually used.

## Completion Criteria

- [x] README sections updated as listed; the example workflow, escalation tier and env
      placeholders added.
- [x] Example tests and the validate command pass.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 10:36 UTC
**Tasks Completed**: Added the README Features (v1.1), data-handling, API, Web application,
and configuration updates; added the owner-review workflow and `min_risk` escalation
example; added v1.1 Compose placeholders and settings notes; extended example tests for
owner-review semantics, escalation risk, and env-template privacy. No design-doc or
application source files changed.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: README.md after=9a9c32e76a1eea5755986f445367cd7b6f03fe27d575152bb54d85528490673e;
examples/workflows/owner-review.yaml after=baa25b27e3d68173e7c78f6294ed95a7b4f6b8c0f836ffca86ecad5e872ab6f2;
examples/workflows/escalation-policies.json after=b7ced8c77816977c3599b895e803588adf7e74a01a08cf92d73d0fd94feac796;
examples/compose/.env.example after=5cd1f624b9cd6c5971218bf93474954b92154c3eb3cffd48a70a32843a989757;
examples/compose/README.md after=f1b52eb8518ed61445a67a3b0c115acf2ae4e8c3b31981a891f3b12b93cfaebd;
tests/examples/test_examples.py after=2e78a1a52e73fc1f8a7fe51712ab7e43c4ea0c8a8abb2ddec2abf3261a0b4773.
**Verification**:
- `uv run --frozen python -m telescreen --help` -> exit 0 (log: `/tmp/telescreen-v11-p20-docs-examples-cli-help.log`).
- `uv run --frozen ruff format --check --no-cache tests/examples` -> exit 0 (log: `/tmp/telescreen-v11-p20-docs-examples-format-verified.log`).
- `uv run --frozen ruff check --no-cache tests/examples` -> exit 0 (log: `/tmp/telescreen-v11-p20-docs-examples-ruff-verified.log`).
- `uv run --frozen ty check tests/examples` -> exit 0 (log: `/tmp/telescreen-v11-p20-docs-examples-ty-verified.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/examples` -> exit 0, 16 passed (log: `/tmp/telescreen-v11-p20-docs-examples-pytest-verified.log`).
- `uv run --frozen python tools/typelint.py` plus owned-path grep `(^|/)tests/examples/` -> exit 0, 346 files clean and no owned-path findings (log: `/tmp/telescreen-v11-p20-docs-examples-typelint-verified.log`).
- `uv run --frozen python -m telescreen workflow validate examples/workflows/owner-review.yaml` -> exit 0 (log: `/tmp/telescreen-v11-p20-docs-examples-validate-verified.log`).
- `grep -c '^- \[ \]' impl-plans/active/v11-p20-docs-examples.md` -> 0 unchecked criteria.
**Notes**: Intermediate verification found and fixed an import-order issue, aligned the
Compose password example with the built-in placeholder detector, and updated the env
placeholder test to accept its exact `changeme` token. The corresponding earlier Ruff
and pytest runs exited nonzero and are superseded by the passing final runs above. No
shared-path or ownership conflicts observed. Plan length remains below 400 lines.
