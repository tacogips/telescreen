# Implementation Conventions (read before any plan)

Every plan under `impl-plans/active/` links here. This file holds the rules shared by all
plans so each plan can stay short. If a plan and this file disagree, the plan wins for
its own scope only.

## 1. Required reading (in order)

1. `CLAUDE.md` (English only, no emojis, strict typing, file size limits).
2. `.claude/skills/python-coding-standards/SKILL.md` (Typing section) and the docstring of
   `tools/typelint.py` (rules TL001-TL010). Python plans only.
3. `design-docs/specs/architecture.md`: sections 4 (layering and ports), 5, 6 (typing
   boundaries), 7 (data handling) and 8 (decomposition).
4. The design sections your plan cites.
5. Your plan file.

## 2. Ownership and shared working directory

- All plans of a wave run at the same time in ONE working directory on ONE branch.
  Other workers are editing other files right now.
- Write only inside your plan's `writePaths`. Your own plan file is in your
  `writePaths` so you can update its Progress Log; do not edit any other plan.
- Never run `git checkout`, `git restore`, `git stash`, `git reset`, `git clean`,
  `git commit`, `git add` or any branch or worktree command. Never delete or revert a file
  you do not own, even if it looks broken.
- Never run `uv lock`, `uv add`, `uv remove`, `bun add` or `bun install` without
  `--frozen-lockfile`. Only plan `p01-foundation` changes dependencies. Missing dependency
  -> stop that task and write a "Reconciliation request" entry in your Progress Log.
- Need a change outside your `writePaths` (new `ErrorCode`, new port method, a bug in a
  dependency plan)? Do not edit it. Write a "Reconciliation request" entry (file, exact
  change, reason), then use a local workaround inside your own paths if one exists.
- Per-edit protocol for every file you modify:
  1. Read the file fresh right before editing; never edit from memory.
  2. Record `shasum -a 256 <file>` before and after in the Progress Log (one line per file
     per session is enough: `path before=<sha|absent> after=<sha>`).
  3. Before finishing, run `git status --porcelain` and check that every path YOU changed
     is inside your `writePaths`. Paths changed by other workers are expected; ignore
     them. If one of your own files changed unexpectedly between your edits (hash drift),
     re-read it, re-apply your intent and log the drift.

## 3. Python rules (summary; typelint is the judge)

- Every parameter, return, variable and class field is annotated. No `Any`, `object`,
  `dict`, `Mapping`, `TypedDict`, `**kwargs`, `**mapping`, bare generics,
  `Callable[..., T]`, `# type: ignore` or `# ty: ignore`.
- Data types are `@dataclass` (stdlib, frozen when possible) or `pydantic.dataclasses`
  for FastAPI bodies. Closed sets are `StrEnum`. Interfaces are `typing.Protocol`.
  Stateful services are dataclasses.
- Subclassing a library class that needs state (for example `logging.Formatter`): use
  `@dataclass(eq=False)` on the subclass and call `super().__init__(...)` in
  `__post_init__`; typelint accepts dataclass-decorated classes.
- Arbitrary JSON uses `telescreen.core.json.JsonValue` (never a recursive `type` alias,
  never pydantic `JsonValue`). Third-party mapping APIs: pass `dataclasses.asdict(x)`,
  `json.loads(...)` or literal dicts inline into the library call; never bind a mapping to
  a variable.
- Exceptions: raise `telescreen.core.errors.TelescreenError(code, detail)`; `detail` is a
  static string, never formatted with content, locators, tokens or user input.
- Logging: `logger: logging.Logger = logging.getLogger(__name__)`; never log raw content,
  request bodies, tokens or secret values. Log codes and ids.
- Fields that can hold raw monitored content, raw locators, tokens or secrets use
  `field(repr=False)`.
- Time: read only through `telescreen.core.clock.Clock`; tests use `FakeClock`.
- IDs: `telescreen.core.ids.new_id()`.
- Files stay under 1000 lines; split by responsibility well before that.
- Module docstring on every new module. Public functions and classes get one-line
  docstrings. Match `tools/typelint.py` style (sections, small functions).

## 4. Database rules

- ORM models live in `telescreen.db.models` (plan p02). Feature packages own repositories
  for their tables (architecture.md section 4) and never write another package's tables
  except through a port.
- Transactions: functions that take `session: AsyncSession` run inside the caller's
  transaction and never commit. Functions without a session parameter open their own
  transaction with `async with sessions.begin() as session:` where `sessions` is an
  `async_sessionmaker[AsyncSession]`.
- SQLite (offline tests) ignores `with_for_update`; correctness must not depend on row
  locks in SQLite tests beyond single-writer behavior.
- Timestamps are UTC-aware; normalize values read back with
  `telescreen.core.clock.ensure_utc`.
- JSON columns are `Text` holding `dump_json(...)` output.

## 5. API rules

- Routers are built by `build_<area>_router(deps: <Area>RouterDeps) -> APIRouter` in
  `src/telescreen/api/routers/<area>.py`. `<Area>RouterDeps` is a frozen dataclass in the
  same module listing what the router needs; plan p28 (assembly) constructs it.
- Bodies are `pydantic.dataclasses.dataclass`, never `BaseModel`. Response field names
  equal the column names in `design-docs/specs/design-data-model.md` (snake_case);
  timestamps are ISO-8601 UTC strings; lists return `{items, next_cursor}`.
- Auth via `telescreen.api.common.auth.require_scope` (plan p10). Errors via
  `TelescreenError`; the common handlers (p10) render `{"error": {...}}`.
- Never echo request content in errors or responses.

## 6. Tests

- pytest with `asyncio_mode = "auto"`, `--import-mode=importlib`; import helpers as
  `from tests.support.<module> import ...`.
- Use `tests.support.samples` (plan p01) for sample secrets and PII. Never write a
  literal secret, token, card number or My Number into any file: gitleaks blocks commits.
- No network: inject `httpx.MockTransport`, fake LLM clients, fake adapters, SQLite
  in-memory sessions from `tests.support.db` (plan p02).
- Integration tests are marked `@pytest.mark.integration` and skip unless
  `TELESCREEN_TEST_DATABASE_URL` / `TELESCREEN_TEST_MEILI_URL` is set.

## 7. Verification protocol

Run from the repository root, in bash. Start EVERY verification shell (every plan's
Verification block, Python or web) with these two lines. `pipefail` makes a piped line
such as `cmd 2>&1 | tee log` return the exit status of `cmd`, not of `tee`. Without it, a
failing test would be recorded as exit 0. `PYTHONDONTWRITEBYTECODE` keeps bytecode out
of source paths.

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
```

Record each command's exit status right after it runs, using one of these equivalent
forms:

- `cmd 2>&1 | tee /tmp/log; echo "exit=$?"` with pipefail enabled;
- `echo "exit=${PIPESTATUS[0]}"` after the piped command;
- `cmd > /tmp/log 2>&1; echo "exit=$?"`.

A recorded `exit=0` from a pipeline run without pipefail is not valid evidence.

Type aliases must use the PEP 695 statement (`type FieldValue = str | int | None`). A
plain assignment `FieldValue = ...` fails typelint TL003.

Scoped checks (must pass with exit 0 for YOUR paths; `<src>` and `<tests>` are your
plan's Python paths):

```bash
uv run --frozen ruff format --check --no-cache <src> <tests>
uv run --frozen ruff check --no-cache <src> <tests>
uv run --frozen ty check <src> <tests>
uv run --frozen pytest -p no:cacheprovider <tests> 2>&1 | tee /tmp/telescreen-<planId>-pytest.log
```

typelint resolves class bases across files, so always run it on the whole repository and
filter for your paths (the grep must print nothing):

```bash
uv run --frozen python tools/typelint.py > /tmp/telescreen-<planId>-typelint.log 2>&1; \
  grep -E '^(<src>|<tests>)' /tmp/telescreen-<planId>-typelint.log
```

Full-repository checks (informational during fanout; record exit status and log path;
failures in files you do not own are expected while other plans are in progress):

```bash
uv run --frozen ruff check --no-cache . > /tmp/telescreen-<planId>-ruff-all.log 2>&1; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-<planId>-pytest-all.log 2>&1; echo "exit=$?"
```

Never run `uv build` into the repository: use
`uv build --out-dir /tmp/telescreen-<planId>-dist`.
Owner ruling (2026-10-08): `/tmp/telescreen-<planId>-*` paths are outside the repository and
outside fanout change tracking. Writing build outputs and logs there is always permitted,
needs no `artifactRoots` entry, and is never a reason to report a blocker. Never run `bun run build` into
`src/web/dist`: use `bun run build --outDir /tmp/telescreen-<planId>-web --emptyOutDir`.

Record in the Progress Log for every command: the command, exit status and log path. An
incomplete log is not a pass.

## 7a. Reporting fixed review findings (owner ruling 2026-10-09)

The progress gate treats every high or medium entry in your step result `findings` or
`risks` as UNRESOLVED and blocks the plan. Therefore:

- A review finding you have fixed and verified goes ONLY in `addressedFeedback`, with the
  changed paths, the verification commands and their passing results. Do not repeat it in
  `findings` or `risks`, and do not mark it "re-review pending": the independent re-review
  happens automatically after your step.
- `findings` and `risks` list only issues that are genuinely still open after your work.
  A residual note that does not need action (for example "integration review is
  downstream") is not a risk and must not be listed.
- Integration tests marked `integration` that are skipped because
  `TELESCREEN_TEST_DATABASE_URL` / `TELESCREEN_TEST_MEILI_URL` are unset are skipped by
  design (offline suite). This is expected, not a risk, and must not be listed.
- If a repair brief forbids editing a file that a mutation check would need, document the
  equivalent in-memory mutation evidence in the plan Progress Log and in
  `addressedFeedback`; this is not a blocker or a risk.

## 8. Progress Log format

Append one entry per session to your own plan's `## Progress Log`:

```markdown
### Session: YYYY-MM-DD HH:MM
**Tasks Completed**: ...
**Tasks In Progress**: ...
**Blockers**: ... (or None)
**Reconciliation requests**: ... (or None)
**Hashes**: path before=... after=...
**Verification**: command -> exit N (log: /tmp/...)
**Notes**: ...
```

Set the plan `**Status**` to `Completed` only when every completion criterion is checked.
Do not move plan files; archiving is done serially after the fanout.
