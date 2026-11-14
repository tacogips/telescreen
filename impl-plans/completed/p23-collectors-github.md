# P23 GitHub Collector Adapter Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-collectors-and-ingestion.md (Per-kind behavior: github; Resource identity; Removal semantics), design-docs/specs/design-map-reduce-sweeps.md (Built-in sweep implementations: github)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p23-collectors-github`
- planPath: `impl-plans/active/p23-collectors-github.md`
- wave: 3
- dependsOn: `p04-ports`
- writePaths:
  - `impl-plans/active/p23-collectors-github.md`
  - `src/telescreen/collectors/github`
  - `tests/collectors/github`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first. Do not create `src/telescreen/collectors/__init__.py`
(owned by p20).

## Intent and Context

This is the built-in GitHub (and GitHub Enterprise) collector. Push and pull_request
webhooks are verified with HMAC. Scheduled scans compare against the last scanned commit
and fall back to a full tree listing when history was rewritten. Sweeps use one shard
per repository and selected branch. PR-ref files are scanned but are never
`sweep_covered`, so sweep or listing reconciliation never resolves them; the workflow's
Police re-check covers leftover refs.

## Non-goals

- No forks, caches or LFS inference (left to the Police `verify_remediation` step).
- No database access.

## Deliverables (`src/telescreen/collectors/github/`)

- `client.py`: `GitHubApi(http, base_url: str, token: SecretValue)`:
  - typed calls `repo(owner, name)`, `repo_by_id(id)`, `org_repos(owner, page)` (falls
    back to `users/{owner}/repos` on 404), `branch(owner, repo, branch)`,
    `compare(owner, repo, base, head)`, `tree(owner, repo, sha, recursive=True)`,
    `contents(owner, repo, path, ref)`, `blob(owner, repo, sha)`,
    `pull_files(owner, repo, number)`;
  - pagination with the `Link` header;
  - 404 -> `ResourceGoneError` for contents and blobs; 403 or 429 with
    `x-ratelimit-remaining: 0` or `retry-after` -> `RateLimited`; other errors ->
    `fetch_failed`.
- `adapter.py`: `GitHubAdapter(api_factory: Callable[[CollectorConfig], GitHubApi], host: str, max_bytes: int)`
  with `kind = CollectorKind.GITHUB`.
  - `verify_event`: `X-Hub-Signature-256` equals `sha256=` + hex HMAC(secret, body),
    compared in constant time. A `ping` event is valid with no work.
  - `resources_from_event`:
    - `push` to `refs/heads/<b>` where the owner matches, the repo is selected (or
      `repos` is empty) and the branch is selected (or is the default branch when
      `branches` is empty): changed refs for the added and modified files of the commits
      (filtered by `path_globs` via `fnmatch`), removed refs for removed files.
      `forced: true` -> `poll_changes=True` only.
    - `pull_request` (opened, synchronize, reopened): changed refs with ref
      `refs/pull/<n>/head` from `pulls/{n}/files` (status not removed).
  - ref_json `{"host","owner","repo","ref","path"}`; raw_locator
    `github:<host>/<owner>/<repo>@<ref>:<path>` (never a commit sha).
  - `fetch`: the contents API at the ref:
    - base64 decode UTF-8 text into line segments with locator `L<n>@<blob-sha[:7]>`;
    - when contents encoding is not base64, or a non-empty file has empty content, fall
      back to the blob API; undecodable content returns `complete=False`;
    - binary or over `max_bytes` -> no segments and `complete=False`;
    - 404 -> `ResourceGoneError`;
    - `sweep_covered = ref.startswith("refs/heads/")` and the branch is selected.
  - `list_changed`: the cursor is canonical JSON `{"<owner>/<repo>@<branch>": "<head sha>"}`.
    For each selected repo and branch:
    - no cursor entry, a compare returning 404, or a non-`ahead`/non-`identical` status
      (history rewritten) -> full tree listing of that ref. When any target needs a full
      listing, emit a full listing over ALL targets with `full_listing=True` on every page
      and `has_more` until the last; `__offset` continues a listing at the cursor-pinned
      head shas, independent of subsequent branch movement;
    - unchanged head shas skip compare; status `identical` produces no changes;
    - otherwise the compare files (added and modified -> changed; removed -> removed;
      renamed -> removed old path plus changed new path).
    The cursor advances to the head sha.
  - `renew_watch` -> `None`.
- `sweep.py`: `GitHubSweep` implements `SweepCapable`.
  - `partition`: corpus `""` -> the selector's repos, or org repo pages. One shard per
    (repo, branch), with branches = the selector's branches or the default branch.
    `shard_key` = sha256 of `<host>/<owner>/<repo>@<branch>`; descriptor
    `{"repo_id": <int>, "branch": "<b>"}`.
  - `shard_resources`: `repo_by_id`, the branch head, then a recursive tree. A truncated
    tree raises `TelescreenError(fetch_failed)` (the whole shard fails, so the sweep is
    partial). For each blob within `path_globs` and the size limit: `blob(sha)` ->
    `fetched`; blob 404 -> `removed`; other errors -> `failed`.

## Key Pitfalls

- Never log file paths, contents or tokens. Paths are part of raw locators.
- GitHub Enterprise: `base_url` comes from `TELESCREEN_GITHUB_API_URL` (assembly passes
  it). `host` for raw locators is the hostname of the web URL. For api.github.com use
  `github.com`.
- `fnmatch` globs apply to the full path. An empty `path_globs` means all paths.
- Compare responses can list at most 300 files. When the response says `truncated` (or
  has 300 files), fall back to a full listing.

## Tests (`tests/collectors/github/`)

All with `httpx.MockTransport`.

- Signature valid, invalid and missing; `ping` -> valid with no work.
- A push with added, modified and removed files -> changed and removed refs; an
  unselected branch -> nothing; `forced: true` -> `poll_changes`.
- A pull_request opened -> refs at `refs/pull/7/head`; a fetch of those gives
  `sweep_covered=False`.
- A fetch of a file with a sample AWS key -> line segments; binary -> `complete=False`;
  contents encoding `none` falls back to the blob endpoint; undecodable blob content ->
  `complete=False`; 404 -> `ResourceGoneError`; 403 with remaining 0 -> `RateLimited`.
- `list_changed`: first run -> a full listing over 2 repos across pages with
  `full_listing` on all pages; pagination uses pinned tree shas even when the branch moves;
  unchanged heads are incremental no-ops; compare `identical` does not trigger a listing;
  an incremental compare -> changed and removed; a compare 404 after a force push -> full
  listing; a rename -> removed plus changed.
- Sweep: 3 repos x 2 branches -> 6 shards with deterministic keys; `shard_resources`
  yields entries; a truncated tree -> fetch_failed.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv run --frozen ruff format --check --no-cache src/telescreen/collectors/github tests/collectors/github
uv run --frozen ruff check --no-cache src/telescreen/collectors/github tests/collectors/github
uv run --frozen ty check src/telescreen/collectors/github tests/collectors/github
uv run --frozen pytest -p no:cacheprovider tests/collectors/github 2>&1 | tee /tmp/telescreen-p23-collectors-github-pytest.log
uv run --frozen python tools/typelint.py src/telescreen/collectors/github tests/collectors/github 2>&1 | tee /tmp/telescreen-p23-collectors-github-typelint-scoped.log
```

## Completion Criteria

- [x] GitHubApi, GitHubAdapter and GitHubSweep delivered with the exact names
- [x] Event, schedule (incremental, rewrite, rename) and sweep tests pass
- [x] Scoped verification passes; logs recorded
- [x] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 22:25 JST
**Tasks Completed**: Implemented typed GitHub REST client, webhook and schedule adapter, repository/branch sweep, and 7 MockTransport tests. Oversized enumerated files yield incomplete scans to prevent false sweep reconciliation; rename path globs apply to old and new paths independently.
**Tasks In Progress**: Downstream test-integrity, adversarial, and combined-tree integration reviews.
**Blockers**: None for p23 scoped implementation.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/collectors/github/__init__.py` before=absent after=b4f438d1cdd67a37973e19ca050661dc9f85a428bec13a6fd6065121142446ee; `client.py` before=absent after=5e9541695667b17852f3af51b5f940a63458c7d558287a62c84c4931dd6e56e1; `adapter.py` before=absent after=8e89b1c528b6853ae6ccd82285cec39120af0cd7f7583ab0f0f4d98e76bcc4a6; `sweep.py` before=absent after=99dbd627077383d67903d32a6b47c594b91f271f79d17fc0fac389c588e023df; `tests/collectors/github/test_github.py` before=absent after=8468218cd39bea318f7a45a9413b7324f167af7ea0f12e081550b9709cfe8a6a. The plan hash before this progress edit was cc0161ea7c79001dd6b2f32258b8ccb6de7739d8aa919a66f3c8fea9596e98dd; post-edit hash is recorded in the plan-local evidence manifest.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (5 files formatted; `/tmp/telescreen-p23-collectors-github-format.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (`/tmp/telescreen-p23-collectors-github-ruff.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/github tests/collectors/github` -> exit 0 (`/tmp/telescreen-p23-collectors-github-ty.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/github 2>&1 | tee /tmp/telescreen-p23-collectors-github-pytest.log` -> exit 0, 7 passed, 0 failed.
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py src/telescreen/collectors/github tests/collectors/github` -> exit 0, 5 files clean (`/tmp/telescreen-p23-collectors-github-typelint-scoped.log`).
- Full-tree `uv run --frozen python tools/typelint.py` -> exit 1 due to diagnostics in concurrent non-p23 plans; the unanchored p23 path filter found no p23 diagnostics (`/tmp/telescreen-p23-collectors-github-typelint-full.log`).
**Notes**: Earlier static attempts found formatting/import-order and untyped local test declarations, which were corrected and rechecked above. Initial zsh wrappers also exited 1 because `status` is a readonly shell variable after the checks completed; all final commands were rerun with `rc` and complete logs. No review acceptance is claimed at implementation time.

### Session: 2026-10-08 22:35 JST
**Tasks Completed**: Self-check found and corrected `fetch` sweep coverage: only selected repositories and branches (or the default branch when the selector has no explicit branches) are now sweep-covered. PR refs remain uncovered. Added a regression assertion.
**Tasks In Progress**: Downstream test-integrity, adversarial, and combined-tree integration reviews.
**Blockers**: None for p23 scoped implementation.
**Reconciliation requests**: None.
**Hashes**: `adapter.py` final=8bfbca3439bfd62df19348733ef1d06ccf7d1f8afa157240790cc3c68f8ea099; `tests/collectors/github/test_github.py` final=58bd487daace1df4e15fb2437d5fe590e6a48d7e0a36ccaa962bdcb12983c802. All other source hashes are unchanged from the previous session entry. Plan hash before this progress update was 2feb03fc119ff740478218dfbd2f217e40306f694696ba85f5fc8294151ccd40; final hash is in the plan-local evidence manifest.
**Verification**: The final source passed `ruff format --check` (5 files formatted), `ruff check`, `ty check`, scoped typelint (5 files clean), and `pytest` (7 passed, 0 failed), all exit 0. Logs: `/tmp/telescreen-p23-collectors-github-{format,ruff,ty,pytest,typelint-scoped}.log`. Full-tree typelint remains exit 1 for non-p23 concurrent-plan diagnostics; its p23 path filter is empty (`/tmp/telescreen-p23-collectors-github-typelint-full.log`).
**Notes**: Exact final commands and observed outcomes are recorded above. No formal review acceptance is claimed.

### Session: 2026-10-09 review redispatch
**Tasks Completed**: Re-ran the p23 scoped gates on the unchanged GitHub collector source and recorded fresh logs. No implementation edits were needed for this review-only redispatch.
**Tasks In Progress**: Native join for test-integrity, adversarial, and branch-evidence review decisions remains downstream of Step 6.
**Blockers**: The low-severity `EVIDENCE-EMPTY-FILTER-LOGS` repair targets `tmp/telescreen-v1-20261008-s319/serial-reconcile-wave3/{lint-diag-owner-attribution.log,accepted-paths-typelint-filter.log}`, neither of which is in p23 `writePaths`. The p23 filter result and exact command are recorded in `tmp/telescreen-v1-20261008-s319/p23-collectors-github/typelint-filter-evidence.log`; serial reconciliation must update the named logs.
**Reconciliation requests**: Serial reconciliation to put the filter command and its output in the two named wave-3 logs, preserving this p23-local evidence.
**Hashes**: Source and test files unchanged: `__init__.py` b4f438d1cdd67a37973e19ca050661dc9f85a428bec13a6fd6065121142446ee; `adapter.py` 8bfbca3439bfd62df19348733ef1d06ccf7d1f8afa157240790cc3c68f8ea099; `client.py` 5e9541695667b17852f3af51b5f940a63458c7d558287a62c84c4931dd6e56e1; `sweep.py` 99dbd627077383d67903d32a6b47c594b91f271f79d17fc0fac389c588e023df; `test_github.py` 58bd487daace1df4e15fb2437d5fe590e6a48d7e0a36ccaa962bdcb12983c802. Plan before=1d235dd953c02be6e0c254901a259e551699b2a8800386d5cb1c80de87ea1cb6.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0, 5 files already formatted (`/tmp/telescreen-p23-collectors-github-format-rerun.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0, all checks passed (`/tmp/telescreen-p23-collectors-github-ruff-rerun.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/github tests/collectors/github` -> exit 0, all checks passed (`/tmp/telescreen-p23-collectors-github-ty-rerun.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/github` -> exit 0, 7 passed and 0 failed (`/tmp/telescreen-p23-collectors-github-pytest-rerun.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py` -> exit 1 for 69 diagnostics outside p23; anchored p23 path filter exit 1 means no matches. Full log: `/tmp/telescreen-p23-collectors-github-typelint-rerun.log`; filter command and interpretation: `tmp/telescreen-v1-20261008-s319/p23-collectors-github/typelint-filter-evidence.log`.
**Notes**: The branch-missing review finding describes the pending native review join, not a code defect. No review decisions or acceptance are claimed by this Step 6 entry.

### Follow-up: serial reconciliation evidence
**Tasks Completed**: Observed the serial reconciliation owner populate both named wave-3 attribution logs after the prior progress entry. `lint-diag-owner-attribution.log` records `total=69 p10=39 p22=30 unattributed=0`; `accepted-paths-typelint-filter.log` records `unattributed diagnostics: 0` and exit 0. The p23 code and tests remain unchanged.
**Tasks In Progress**: Native join for test-integrity, adversarial, and branch-evidence review decisions remains downstream of Step 6.
**Blockers**: None for p23 implementation. No repair was made outside p23 `writePaths`.
**Reconciliation requests**: None.
**Evidence**: `tmp/telescreen-v1-20261008-s319/serial-reconcile-wave3/lint-diag-owner-attribution.log`; `tmp/telescreen-v1-20261008-s319/serial-reconcile-wave3/accepted-paths-typelint-filter.log`; p23 source hashes and fresh verification logs are recorded in the 2026-10-09 redispatch entry above.

### Session: 2026-10-09 adversarial repair
**Tasks Completed**: Fixed `ADV-P23-CONTENTS-LARGE-FILE-FALSE-COMPLETE`: `fetch` falls back to blob content for non-base64 or empty nonempty-file responses, and returns incomplete when blob content is unavailable or empty. Fixed `ADV-P23-IDENTICAL-COMPARE-FULL-LISTING`: unchanged heads skip compare, `identical` is a no-op, and full-listing continuations use cursor-pinned tree shas while `__offset` is present. Added MockTransport coverage for blob fallback and fail-closed empty content, steady state, no compare during pagination, and a branch moving between pages. Updated the plan verification to run scoped typelint.
**Tasks In Progress**: Independent adversarial re-review of both corrections and downstream branch-evidence/native-join reviews.
**Blockers**: None for p23 implementation.
**Reconciliation requests**: None.
**Hashes**: `adapter.py` before=8bfbca3439bfd62df19348733ef1d06ccf7d1f8afa157240790cc3c68f8ea099 after=e517fb9620a5b2d5cd4c23d3d93413c052decd9c86b483cb9bb3768021e4fd5c; `test_github.py` before=58bd487daace1df4e15fb2437d5fe590e6a48d7e0a36ccaa962bdcb12983c802 after=9b20d35b3f08c4b5511502b20f809c8eea166baa101425913e3ea460a5d195b1; plan before=8eb30bc4ef817081c59c27dcd4d50f7deb77db34bbffca7dec24a4674a215c3f.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0, 5 files formatted (`/tmp/telescreen-p23-collectors-github-format-revision-final3.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (`/tmp/telescreen-p23-collectors-github-ruff-revision-final3.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/github tests/collectors/github` -> exit 0 (`/tmp/telescreen-p23-collectors-github-ty-revision-final3.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/github` -> exit 0, 10 passed and 0 failed (`/tmp/telescreen-p23-collectors-github-pytest-revision-final3.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py src/telescreen/collectors/github tests/collectors/github` -> exit 0, 5 files clean (`/tmp/telescreen-p23-collectors-github-typelint-revision-final3.log`).
**Prior Verification**: The initial added pagination test fixture failed before behavioral assertions because `httpx.Response(json=...)` cannot serialize `JsonValue` (`/tmp/telescreen-p23-collectors-github-pytest-revision-001.log`, exit 1); the cursor assertions initially failed ty optionality checks (`/tmp/telescreen-p23-collectors-github-ty-revision-final.log`, exit 1). Both were corrected, and final-source reruns above pass. The final test fix serializes the fixture with `dump_json`.
**Notes**: The pre-repair in-memory reproductions reported `encoding=none -> complete=True, segments=0` and steady-state `identical -> full_listing=True`; those behaviors are covered by the new regression assertions and were corrected. Test-integrity previously accepted p23; adversarial rejection remains pending independent re-review of these fixes.

### Follow-up: explicit identical-status regression
**Tasks Completed**: Added a direct case where the cursor SHA differs from the current head and GitHub compare responds with `status="identical"`; the result is incremental with no refs. The unchanged-head case still asserts compare is skipped. A read-only independent code review found no material defect in either adversarial correction.
**Tasks In Progress**: Formal adversarial re-review and branch-evidence/native join remain downstream; the independent read-only check is not formal review acceptance.
**Blockers**: None for p23 implementation.
**Reconciliation requests**: None.
**Hashes**: `adapter.py` unchanged at e517fb9620a5b2d5cd4c23d3d93413c052decd9c86b483cb9bb3768021e4fd5c; `test_github.py` before=9b20d35b3f08c4b5511502b20f809c8eea166baa101425913e3ea460a5d195b after=1e9aeb1ffa51b6e60f82e8e00ba5ee620c85e633c8a595c5d24b4d345359b4bb; plan before=35a966bf97730e765218f60043050af84080bfed5a16ddcc864f5657c2d124a0.
**Verification**:
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (`/tmp/telescreen-p23-collectors-github-format-revision-final4.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (`/tmp/telescreen-p23-collectors-github-ruff-revision-final4.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/github tests/collectors/github` -> exit 0 (`/tmp/telescreen-p23-collectors-github-ty-revision-final4.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/github` -> exit 0, 10 passed and 0 failed (`/tmp/telescreen-p23-collectors-github-pytest-revision-final4.log`).
- `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen python tools/typelint.py src/telescreen/collectors/github tests/collectors/github` -> exit 0, 5 files clean (`/tmp/telescreen-p23-collectors-github-typelint-revision-final4.log`).
**Notes**: No code behavior changed in this follow-up; final4 reran all scoped gates on the final test source. Formal Opus adversarial acceptance is still pending.
