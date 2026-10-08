# P23 GitHub Collector Adapter Implementation Plan

**Status**: Ready
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
    - base64 decode, UTF-8 text, segments per line with locator `L<n>@<blob-sha[:7]>`;
    - binary or over `max_bytes` -> no segments and `complete=False`;
    - 404 -> `ResourceGoneError`;
    - `sweep_covered = ref.startswith("refs/heads/")` and the branch is selected.
  - `list_changed`: the cursor is canonical JSON `{"<owner>/<repo>@<branch>": "<head sha>"}`.
    For each selected repo and branch:
    - no cursor entry, a compare returning 404, or a non-`ahead` status (history
      rewritten) -> full tree listing of that ref. When any target needs a full listing,
      emit a full listing over ALL targets with `full_listing=True` on every page and
      `has_more` until the last;
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
  404 -> `ResourceGoneError`; 403 with remaining 0 -> `RateLimited`.
- `list_changed`: first run -> a full listing over 2 repos across pages with
  `full_listing` on all pages; an incremental compare -> changed and removed; a compare
  404 after a force push -> full listing; a rename -> removed plus changed.
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
uv run --frozen python tools/typelint.py > /tmp/telescreen-p23-collectors-github-typelint.log 2>&1; grep -E '^(src/telescreen/collectors/github|tests/collectors/github)' /tmp/telescreen-p23-collectors-github-typelint.log   # prints nothing
```

## Completion Criteria

- [ ] GitHubApi, GitHubAdapter and GitHubSweep delivered with the exact names
- [ ] Event, schedule (incremental, rewrite, rename) and sweep tests pass
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
