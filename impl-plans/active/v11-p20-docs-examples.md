# V11-P20 Docs and Examples: README v1.1 Features, Owner-Review Workflow Example, Compose Settings

**Status**: Ready
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

- [ ] README sections updated as listed; the example workflow, escalation tier and env
      placeholders added.
- [ ] Example tests and the validate command pass.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
