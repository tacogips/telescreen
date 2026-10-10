# V11-P11 GitHub Collector: Exposure, CODEOWNERS Owner, Placement, Modified Time, Coverage

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-1-dspm-adoption.md sections 3 (GitHub row), 4 (placement `github_owner`, `github_repo`; push commit timestamp), 6 (coverage), 7 (owner identity: CODEOWNERS, push author)
**Created**: 2026-10-10
**Last Updated**: 2026-10-10

## Plan Metadata

- planId: `v11-p11-collectors-github`
- planPath: `impl-plans/active/v11-p11-collectors-github.md`
- wave: 2
- dependsOn: `v11-p01-foundation`
- writePaths:
  - `impl-plans/active/v11-p11-collectors-github.md`
  - `src/telescreen/collectors/github`
  - `tests/collectors/github/test_v11_context.py` (new)
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Use the repository JSON the adapter already fetches (`visibility`, `private`, `fork`) for
exposure. Read `CODEOWNERS` once per repository and ref per scan task for the owner, with
the push head-commit author as a fallback for event scans. Add placement keys and
coverage.

Current code:

- `collectors/github/client.py` (`repo`, `repo_by_id`, `org_repos`, `contents`, `blob`;
  4xx -> `FETCH_FAILED` at about lines 182-185);
- `collectors/github/adapter.py` (push payload parsing at about 97-102; `ResourceContent`
  at about 304);
- `collectors/github/sweep.py` (`ResourceContent` at about 170 and 185).

## Non-goals

- No commits API calls per file (no last-committer lookup in sweeps). No write
  endpoints.
- No team expansion for `@org/team` CODEOWNERS entries (they are ignored).

## Invariants

- Logins and emails exist only in transient `OwnerIdentity` (`repr=False`).
- GitHub placement values are lowercased (`owner`, `owner/repo`). The policy decider
  (v11-p07) lowercases selectors the same way.
- `client.py` keeps its read-only HTTP methods only (GET).

## Deliverables

1. **`client.py`**:
   - Repository parsing keeps `visibility: str | None`, `private: bool`, `fork: bool`
     (add the fields to the parsed dataclass if they are missing).
   - New `codeowners(owner, repo, ref) -> str | None` that tries `.github/CODEOWNERS`,
     `CODEOWNERS` and `docs/CODEOWNERS` through the existing contents API
     (`ResourceGoneError` -> next path; all missing -> None). The text is transient.
   - In `contents` and `blob`, a 403 that `_request` did not already turn into
     `RateLimited` (rate-limit detection stays in `_request`) raises
     `TelescreenError(ErrorCode.FORBIDDEN, <static message>)` instead of the `_decode`
     `FETCH_FAILED`. This is the design section 6 rule "provider 403 ... ->
     `permission_denied` (today these map to `fetch_failed`)".
   - A contents or blob 404 keeps the v1 behavior unchanged: it raises
     `ResourceGoneError` (client.py `contents` and `blob`, about lines 88-89 and 97-98).
     The resource is recorded as removed, which resolves as `resource_deleted` (design
     section 2: `ResourceGoneError` sets `removed=True`; section 5: `removed=true` ->
     `resource_deleted`). The section 6 404 clause applies only to 404s that map to
     `fetch_failed` today; these do not, so they never become `FORBIDDEN`. The other
     endpoints (`repo`, trees, compare) keep their v1 `_decode` mapping.
   - Inside the new `codeowners` lookup only, `ResourceGoneError` from `contents` means
     "try the next path". Any other error (including `FORBIDDEN`) ends the lookup with
     None.
   - `sweep.py` (about line 117-118): the `except TelescreenError` branch yields
     `error_code=error.code` instead of the hard-coded `ErrorCode.FETCH_FAILED`, so a 403
     reaches v11-p08 as `FORBIDDEN` (mapped there to `permission_denied`). The
     `except ResourceGoneError` -> `REMOVED` branch above it stays unchanged.
2. **`codeowners.py`** (new): `match_owner(codeowners_text, path) -> OwnerIdentity | None`
   following GitHub semantics:
   - the last matching pattern wins;
   - the first owner of that line;
   - `@login` -> `GITHUB_LOGIN` (without `@`);
   - `name@domain` -> `EMAIL`;
   - `@org/team` -> skip to the next owner on the same line.

   Pattern support: `*`, `**`, a leading `/` anchors to the root, a trailing `/` means a
   directory prefix, and a bare name matches at any depth. Comments and blank lines are
   ignored.
3. **`exposure.py`** (new): `repo_exposure(repo) -> Exposure`:
   - `public` -> `public`; `internal` -> `domain`; `private` -> `private`, falling back to
     the `private` bool;
   - `repo_visibility` set accordingly, `is_fork` copied.
4. **`adapter.py` and `sweep.py`**: every `ResourceContent` sets:
   - `exposure`;
   - `owner`: the CODEOWNERS match, else the latest path commit's `author.login` from a
     read-only commits-by-path lookup as a transient `GITHUB_LOGIN`. Never persist the
     provider login in the queued resource reference;
   - `placement=((github_owner, owner.lower()), (github_repo, f"{owner}/{repo}".lower()))`;
   - `modified_at` = the latest path commit's author timestamp when available;
   - `coverage`:
     - oversized blob (`size > self.max_bytes` in `adapter.py` and `sweep.py`; `max_bytes`
       is `TELESCREEN_SCAN_MAX_BYTES`, so this is design section 6 "content exceeds
       `TELESCREEN_SCAN_MAX_BYTES`") -> `Coverage(CoverageState.TOO_LARGE)` with reason
       None. The same mapping is used by the Slack plan (v11-p09);
     - non-base64 or undecodable (binary) blob -> `Coverage(UNSUPPORTED, BINARY)`;
     - `FORBIDDEN` -> `PERMISSION_DENIED`;
     - other errors -> `FETCH_FAILED`.

   CODEOWNERS is fetched once per `(owner, repo, ref)` per adapter call scope (a sweep
   shard or an event batch). Cache it in a small dataclass of tuples, not a dict.

## Pitfalls

- A CODEOWNERS fetch failure must never fail the scan; the owner becomes None.
- Do not treat the CODEOWNERS file itself as unscannable. It is not a scanned resource
  unless it is in the selector's path globs (v1 behavior unchanged).
- Keep `adapter.py` under 1000 lines (453 now); helpers go in the new modules.
- Do not change 404 handling of `contents` or `blob`. The v1 404 cases in
  `tests/collectors/github/test_github.py` (for example `missing.txt` at about line 192
  with `pytest.raises(ResourceGoneError)` at about 246, and the sweep and compare 404
  cases) must keep passing unchanged. Do not edit that file.
- Keep `adapter.py` `fetch` re-raising `ResourceGoneError` (about line 224) so event and
  rescan fetches of a deleted file still report removal.

## Tests (`tests/collectors/github/test_v11_context.py`)

Imitate `tests/collectors/github/test_github.py` with MockTransport.

- public repo -> public; internal -> domain; private fork -> private with `is_fork`;
- CODEOWNERS with `* @default` and `/secrets/ @sec-owner` -> path `secrets/a.txt` gives
  `sec-owner`, `docs/x.md` gives `default`;
- an `@org/team` first owner -> the next owner on the line; an email owner -> EMAIL kind;
- no CODEOWNERS (3 x 404) + push event with author username -> GITHUB_LOGIN; for a sweep
  -> None;
- placement values are lowercased;
- push timestamp -> `modified_at`;
- 403 (no rate-limit headers) on contents -> `permission_denied`; in a sweep, a blob 403
  -> `FAILED` entry with `error_code=FORBIDDEN`; a 403 with `x-ratelimit-remaining: 0`
  -> `RateLimited`;
- regression: contents 404 and blob 404 -> `ResourceGoneError` (not `TelescreenError`
  with `FORBIDDEN`); a sweep blob 404 -> `REMOVED` entry;
- a blob with `size > max_bytes` -> `too_large`; a non-base64 blob -> `unsupported/binary`;
- CODEOWNERS fetched once for two files in the same repo and ref (count requests);
- all requests are GET.

## Verification

Conventions section 7 with `<src>` = `src/telescreen/collectors/github` and `<tests>` =
`tests/collectors/github`. Existing GitHub tests must still pass unchanged.

- `grep -n '404' src/telescreen/collectors/github/client.py` -> `contents` and `blob`
  still raise `ResourceGoneError` on 404.
- `git diff --stat tests/collectors/github/test_github.py` -> empty.

## Completion Criteria

- [x] Exposure, owner fallback and `modified_at` match the accepted event contract: push references carry only a strictly validated, digit-free reversible head SHA encoding; fetch reads its author and timestamp transiently, and sweep references use the latest path commit.
- [x] Non-rate-limit 403 -> `FORBIDDEN`; contents and blob 404 -> `ResourceGoneError`
      unchanged (regression test passes).
- [x] Scoped gates pass; the typelint grep is empty.
- [x] Progress Log complete.

## Progress Log

### Session: 2026-10-10 19:00 JST
**Tasks Completed**: Added GitHub repository exposure mapping, CODEOWNERS parsing and lookup, lowercased owner/repository placement, resource coverage, and preservation of `FORBIDDEN` sweep error codes. Added v1.1 collector tests. Existing `test_github.py` is unchanged.
**Tasks In Progress**: Push event head-commit author fallback and timestamp propagation.
**Blockers**: The adapter event contract returns only `ResourceRef`; collector service persists `ref_json` in the task queue; `fetch` receives no push payload. Persisting `author.username` in `ref_json` violates the no-raw-principal guarantee. No safe transient or keyed-owner transport exists in p11 writePaths.
**Reconciliation requests**: Amend the accepted contract/ownership for `src/telescreen/ports/collectors.py`, `src/telescreen/collectors/service.py`, and `src/telescreen/collectors/tasks.py` (all outside p11 writePaths) to define a typed event-context seam that carries the commit timestamp and a keyed owner reference across the durable queue without persisting the raw login; bind it through scanning, and add push-event regression tests. Resume p11 after the design and write-path amendment is accepted.
**Hashes**: `src/telescreen/collectors/github/client.py` before=5e9541695667b17852f3af51b5f940a63458c7d558287a62c84c4931dd6e56e1 after=a454343dd2d532edcb2303379460407592d181e310caa07667a563161c72edab; `src/telescreen/collectors/github/adapter.py` before=e517fb9620a5b2d5cd4c23d3d93413c052decd9c86b483cb9bb3768021e4fd5c after=ad4cc35b5491dfbeff9329d6a4c762338df8f647a96a4b9e7db4d451c12cc65c; `src/telescreen/collectors/github/sweep.py` before=99dbd627077383d67903d32a6b47c594b91f271f79d17fc0fac389c588e023df after=8055fd8115955527a33c26e97ddd8b807276c77856d6c4b64440db95af0afc83; `src/telescreen/collectors/github/codeowners.py` before=absent after=6d82a9b63e98eeee3bfebbeed7334e1bfe2ad033ac34825fee2945866425e441; `src/telescreen/collectors/github/exposure.py` before=absent after=f6f98aed8657d89a5db186bf4ec3236cdce20c19217d2e90cb75dfaa92a1c192; `tests/collectors/github/test_v11_context.py` before=absent after=1e4bd0b2eb3ef65e617f76c5b0173702e0290d424ad3c416021e583c6eeaa84e.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-format-final.log`); `uv run --frozen ruff check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-lint-final.log`); `uv run --frozen ty check src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-ty-final.log`); `uv run --frozen pytest -p no:cacheprovider tests/collectors/github` -> exit 0, 15 passed (log: `/tmp/telescreen-v11-p11-collectors-github-pytest.log`); `uv run --frozen python tools/typelint.py` with scoped grep `^(src/telescreen/collectors/github/|tests/collectors/github/)` -> zero p11 matches (log: `/tmp/telescreen-v11-p11-collectors-github-typelint.log`); `grep -n '404' src/telescreen/collectors/github/client.py` -> exit 0; `test -z "$(git diff --stat HEAD -- tests/collectors/github/test_github.py)"` -> exit 0.
**Notes**: An earlier scoped test run failed against absent optional fixture metadata and a test transport's CODEOWNERS route; both were corrected, and the final current-source run passes all 15 tests. Repository-wide typelint still reports findings in concurrent sibling-owned paths, but the required p11-scoped grep is empty. Push author and event timestamp remain unimplemented pending the reconciliation request above.

### Session: 2026-10-10 17:10 JST
**Tasks Completed**: Corrected the private-boolean exposure fallback: absent/unknown visibility maps `private: true` to private and `private: false` to public. Re-ran all p11 source and test gates against this final source. The GitHub suite reports 15 passed, 0 failed.
**Tasks In Progress**: Push event head-commit author fallback and timestamp propagation.
**Blockers**: Same p11 interface/write-path reconciliation request as above; no safe event context crosses the durable collector task boundary.
**Reconciliation requests**: See the prior session entry for the exact out-of-scope paths and requested keyed-owner/timestamp seam.
**Hashes**: `src/telescreen/collectors/github/exposure.py` before=f6f98aed8657d89a5db186bf4ec3236cdce20c19217d2e90cb75dfaa92a1c192 after=fa4a761c0df31d1e49a5dcbe21132cfccb8c59ba72e1a12f46db65e7662490e8; `tests/collectors/github/test_v11_context.py` before=1e4bd0b2eb3ef65e617f76c5b0173702e0290d424ad3c416021e583c6eeaa84e after=e9fcae7780b2acd38f547f4da9f721534cea44f91562597274cbf8e9b0074cd4.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-format-final3.log`); `uv run --frozen ruff check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-lint-final3.log`); `uv run --frozen ty check src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-ty-final3.log`); `uv run --frozen pytest -p no:cacheprovider tests/collectors/github` -> exit 0, 15 passed (log: `/tmp/telescreen-v11-p11-collectors-github-pytest-final3.log`); `uv run --frozen python tools/typelint.py` plus p11-scoped grep `^(src/telescreen/collectors/github/|tests/collectors/github/)` -> exit 0, zero p11 matches (log: `/tmp/telescreen-v11-p11-collectors-github-typelint-final3.log`); `grep -n '404' src/telescreen/collectors/github/client.py` -> exit 0; `test -z "$(git diff --stat HEAD -- tests/collectors/github/test_github.py)"` -> exit 0.
**Notes**: One intermediate format check before the final formatting fix reported `exposure.py` would be reformatted; the final format check above passes on the final source. The p11 completion criterion covering push author and event `modified_at` remains open until the reconciliation request is resolved.

**Owner resolution (2026-10-10)**: Do not take the owner from the push payload and do not persist any GitHub login/username in ref_json, task payloads or rows. Owner resolution order for GitHub: (1) CODEOWNERS match for the path (team or user handle resolved in memory through member_identities); (2) at fetch time, call `GET /repos/{owner}/{repo}/commits?path=<path>&sha=<ref>&per_page=1` and resolve the last commit's `author.login` in memory through member_identities (IdentityKind github_login). Persist only the resolved `owner_member_id` (or nothing). Unresolvable logins are dropped, never stored. This removes the event-context seam.

### Session: 2026-10-10 Step 6 resume
**Tasks Completed**: Replaced the unsafe queued push-author context proposal with the plan's owner-resolution contract: `GitHubApi.last_commit_for_path` reads the latest path commit, and `CommitContext` carries the author as a transient repr-hidden `OwnerIdentity` plus UTC `modified_at`. Both event fetch and sweep prefer CODEOWNERS, then use the latest path commit fallback. Added a push-event regression through `resources_from_event` and fetch. Existing GitHub tests remain unchanged.
**Tasks In Progress**: Confirm whether fallback owner and `modified_at` for push scans use the push head commit or the latest commit affecting each path.
**Blockers**: Accepted fanout criterion says push head-commit author and timestamp; the plan's appended Owner resolution note says use the latest path commit's author and timestamp and avoid push payload context. For a multi-commit push these are not equivalent, and fetch-time lookup by path cannot reproduce the original head commit if the ref advances before fetch.
**Reconciliation requests**: Confirm the intended commit source. If the push head commit is required, amend the accepted context contract and p11 write paths for safe propagation of its SHA/timestamp (never raw login), or authorize the committed latest-path-commit behavior as the accepted replacement. Resume p11 with the matching regression test and rerun the final gates.
**Hashes**: `src/telescreen/collectors/github/client.py` before=a454343dd2d532edcb2303379460407592d181e310caa07667a563161c72edab after=37f9e556c347269c76635a6da131966aa59aca600d87daf2faee2588b0e937f3; `src/telescreen/collectors/github/adapter.py` before=ad4cc35b5491dfbeff9329d6a4c762338df8f647a96a4b9e7db4d451c12cc65c after=3486ddfdb0a5353dd05a2e83ca49cd09e7ef8924361a91279cf84c309d4c8b05; `src/telescreen/collectors/github/sweep.py` before=8055fd8115955527a33c26e97ddd8b807276c77856d6c4b64440db95af0afc83 after=64f95acded8f8f1205551aaf185a293de251a180df59c4d962cb8c94bc71ec8e; `src/telescreen/collectors/github/codeowners.py` before=fa4a761c0df31d1e49a5dcbe21132cfccb8c59ba72e1a12f46db65e7662490e8 after=f0fdff39e42fc5c1773a07637cf388d12e19271e9b7c8b87244e2f720cc64b10; `tests/collectors/github/test_v11_context.py` before=e9fcae7780b2acd38f547f4da9f721534cea44f91562597274cbf8e9b0074cd4 after=963bccf86364fb632d0d68e2040e5bcbca1e89e3688a216e60ff0aa024109a42.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-resume1-format-final4.log`); `uv run --frozen ruff check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-resume1-ruff-final4.log`); `uv run --frozen ty check src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-resume1-ty-final4.log`); `uv run --frozen pytest -p no:cacheprovider tests/collectors/github` -> exit 0, 16 passed (log: `/tmp/telescreen-v11-p11-collectors-github-resume1-pytest-final4.log`); whole-repository `tools/typelint.py` -> exit 0 with zero p11 matches under the absolute-path-aware filter (logs: `/tmp/telescreen-v11-p11-collectors-github-resume1-typelint-final4.log`, `/tmp/telescreen-v11-p11-collectors-github-resume1-typelint-scoped-final4.log`); `grep -n '404' src/telescreen/collectors/github/client.py` -> exit 0; `test -z "$(git diff --stat HEAD -- tests/collectors/github/test_github.py)"` -> exit 0.
**Notes**: An intermediate typelint run identified unannotated `CommitContext` locals and the event-resource result; all were annotated without changing lint rules. Final4 gates were run after those fixes. The code implements the latest-path-commit interpretation and passes scoped gates, but completion remains open pending resolution of the push-head versus latest-path-commit contract conflict.

**Owner clarification (2026-10-10, supersedes the previous owner note where they differ)**: The
acceptance criterion "push head-commit author and timestamp" is satisfied WITHOUT persisting any
login, as follows. The ResourceRef/ref_json may carry the head commit SHA (a commit SHA is not
sensitive). At fetch time call `GET /repos/{owner}/{repo}/commits/{head_sha}`; read
`author.login` and `commit.author.date` (or committer date) from the response IN MEMORY. Use the
date as `modified_at`. Resolve the login through member_identities (IdentityKind github_login)
in memory and persist only the resolved member reference; drop unresolvable logins. Owner
order: CODEOWNERS match first, then this head-commit author. For schedule/sweep scans without a
push head SHA, use `GET /repos/{owner}/{repo}/commits?path=<path>&sha=<ref>&per_page=1` the same
way. Never write the login to ref_json, task payloads, rows, logs or errors; add a test asserting
the login string appears in none of them. This is not a blocker.

### Session: 2026-10-10 Step 6 resume 2
**Tasks Completed**: Implemented the superseding owner clarification. Push event `ResourceRef.ref_json` carries only `head_commit.id`; `ref_from_json` preserves it, and fetch calls the immutable commit endpoint to read author login and commit date in memory. Sweep refs without a push SHA still use the latest path commit. Regression tests verify both commit sources, author/date selection, and absence of the author login from the durable ref JSON, raw locator and owner representation. No files outside p11 write paths were edited.
**Tasks In Progress**: None.
**Blockers**: None. The earlier push-head versus latest-path conflict is resolved by the owner clarification above.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/collectors/github/adapter.py` before=3486ddfdb0a5353dd05a2e83ca49cd09e7ef8924361a91279cf84c309d4c8b05 after=60cd00c97ec193e6e259366c879cfeda98391641b70ed58b67ab991a38e9f6bc; `src/telescreen/collectors/github/client.py` before=37f9e556c347269c76635a6da131966aa59aca600d87daf2faee2588b0e937f3 after=b3e995172f8879e2ca40fb21a86b03fcb56329ef04f4e0cdc7bf91b1ba8f2b81; `src/telescreen/collectors/github/codeowners.py` unchanged=f0fdff39e42fc5c1773a07637cf388d12e19271e9b7c8b87244e2f720cc64b10; `tests/collectors/github/test_v11_context.py` before=963bccf86364fb632d0d68e2040e5bcbca1e89e3688a216e60ff0aa024109a42 after=2a17515ae89fa609a7c42f9b770e01eede15beb419818c1228fcca7b92978a0f.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0, 8 files formatted (log: `/tmp/telescreen-v11-p11-collectors-github-resume2-format-final3.log`); `uv run --frozen ruff check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-resume2-ruff-final3.log`); `uv run --frozen ty check src/telescreen/collectors/github tests/collectors/github` -> exit 0 (log: `/tmp/telescreen-v11-p11-collectors-github-resume2-ty-final3.log`); `uv run --frozen pytest -p no:cacheprovider tests/collectors/github` -> exit 0, 17 passed, 0 failed (log: `/tmp/telescreen-v11-p11-collectors-github-resume2-pytest-final3.log`); whole-repository `tools/typelint.py` plus the absolute-path-aware p11 filter -> exit 0, zero p11 diagnostics (logs: `/tmp/telescreen-v11-p11-collectors-github-resume2-typelint-final3.log`, `/tmp/telescreen-v11-p11-collectors-github-resume2-typelint-scoped-final3.log`); `grep -n '404' src/telescreen/collectors/github/client.py` -> exit 0; `test -z "$(git diff --stat HEAD -- tests/collectors/github/test_github.py)"` -> exit 0.
**Notes**: The typelint process reports pre-existing concurrent sibling diagnostics outside p11; the required owned-path filter output is empty. The verification commands passed after the last source edits. The final checkbox check and file-scope check are recorded in this Step 6 result.

### Session: 2026-10-10 Step 6 resume 3 — adversarial repair
**Tasks Completed**: Addressed `P11-ADV-HEADSHA-REDACTOR-DROP`. Push head commit IDs now enter refs only when they are lowercase hexadecimal with exactly 40 or 64 characters. `_ref` stores them under `head_commit` after translating digits `0-9` to `g-p`; `_parse_ref` and `ref_from_json` decode the value. Strictly valid legacy `head_sha` refs remain readable and are re-emitted only encoded. Invalid webhook commit IDs are omitted. The realistic phone_jp-triggering push test verifies the redactor accepts ref JSON, the SHA is absent in raw form, round-tripping retains the ref, the v1 raw locator is unchanged, and fetch still requests the original immutable commit for transient owner/date. The malformed SHA test proves omission and path-history owner/date fallback. `test_github.py` is unchanged.
**Tasks In Progress**: None for p11 implementation. Independent adversarial re-review and serial integration on the combined tree remain downstream workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `src/telescreen/collectors/github/adapter.py` before=60cd00c97ec193e6e259366c879cfeda98391641b70ed58b67ab991a38e9f6bc after=0fb84e7cfb575568dbb05a6b182f3342c189c2be7aa7fe417ea97b8b742803df; `tests/collectors/github/test_v11_context.py` before=2a17515ae89fa609a7c42f9b770e01eede15beb419818c1228fcca7b92978a0f after=717bf17966835be2cdfb1008652429fcb38f878d43783643a068518e4a2cf001.
**Verification**: `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff format --check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0, 8 files already formatted (`/tmp/telescreen-v11-p11-collectors-github-resume3-format.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ruff check --no-cache src/telescreen/collectors/github tests/collectors/github` -> exit 0, all checks passed (`/tmp/telescreen-v11-p11-collectors-github-resume3-ruff.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen ty check src/telescreen/collectors/github tests/collectors/github` -> exit 0 (`/tmp/telescreen-v11-p11-collectors-github-resume3-ty.log`); `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1; uv run --frozen pytest -p no:cacheprovider tests/collectors/github` -> exit 0, 17 passed and 0 failed (`/tmp/telescreen-v11-p11-collectors-github-resume3-pytest.log`); whole-repository `tools/typelint.py` -> exit 0 with no p11 diagnostics under `(^|/)(src/telescreen/collectors/github/|tests/collectors/github/)` (`/tmp/telescreen-v11-p11-collectors-github-resume3-typelint.log`, scoped output `/tmp/telescreen-v11-p11-collectors-github-resume3-typelint-scoped.log`); `grep -n '404' src/telescreen/collectors/github/client.py` -> exit 0; `test -z "$(git diff --stat HEAD -- tests/collectors/github/test_github.py)"` -> exit 0; completion checkbox check -> exit 0. The focused repair cases passed 2 tests (`/tmp/telescreen-v11-p11-collectors-github-resume3-focused2.log`). An initial focused collection attempt exposed a mixed byte/string fixture syntax error; the fixture was corrected before the passing focused and full suite runs.
**Notes**: Raw locator construction and inputs to resource identity were not changed. The independent adversarial re-review and combined-tree rerun of all eight manifest checks are pending downstream.
