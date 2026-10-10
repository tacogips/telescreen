# V11-P11 GitHub Collector: Exposure, CODEOWNERS Owner, Placement, Modified Time, Coverage

**Status**: Ready
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
   - `owner`: the CODEOWNERS match, else (push events only) the head commit
     `author.username` as `GITHUB_LOGIN`. Add `username` and `timestamp` to the push
     payload dataclass;
   - `placement=((github_owner, owner.lower()), (github_repo, f"{owner}/{repo}".lower()))`;
   - `modified_at` = the push head commit `timestamp` for event scans, else None;
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

- [ ] Exposure, owner (CODEOWNERS and push author), placement, modified time and
      coverage as specified.
- [ ] Non-rate-limit 403 -> `FORBIDDEN`; contents and blob 404 -> `ResourceGoneError`
      unchanged (regression test passes).
- [ ] Scoped gates pass; the typelint grep is empty.
- [ ] Progress Log complete.

## Progress Log

(append sessions per conventions section 8)
