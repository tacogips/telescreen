# P01 Foundation Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/architecture.md (sections 4, 5, 6, 8), design-docs/specs/command.md
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p01-foundation`
- planPath: `impl-plans/active/p01-foundation.md`
- wave: 1
- dependsOn: none
- writePaths:
  - `impl-plans/active/p01-foundation.md`
  - `pyproject.toml`
  - `uv.lock`
  - `mise.toml`
  - `.gitignore`
  - `README.md`
  - `.venv`
  - `src/web/package.json`
  - `src/web/bun.lock`
  - `src/web/node_modules`
  - `src/telescreen/core`
  - `src/telescreen_police_sdk/__init__.py`
  - `src/telescreen_police_sdk/py.typed`
  - `tests/conftest.py`
  - `tests/support/__init__.py`
  - `tests/support/samples.py`
  - `tests/core`
- sharedPaths: none
- artifactRoots: `.venv`, `src/web/node_modules`

Read `impl-plans/conventions.md` first.

## Intent and Context

Every other plan builds on this one. It does four things:

- adds the complete v1 dependency set (both lockfiles);
- adds the tooling configuration;
- writes the final README;
- creates the L0 package `telescreen.core`, whose shared types (`JsonValue`, settings,
  errors, enums, clock, durations, secrets) are frozen for all later plans.

Repository today: `src/telescreen/{__init__,__main__,cli}.py` is a scaffold (keep it
unchanged), `tools/typelint.py` is the strict linter, and `uv.lock` pins `ty` 0.0.85.

## Non-goals

- No feature code (db, detection, API, UI source files).
- Do not touch `src/telescreen/__init__.py`, `cli.py`, `__main__.py`, `tests/test_package.py`,
  `tools/`, or `design-docs/`.
- Do not weaken typelint, ruff ANN/PGH or ty settings. Do not upgrade `ty`.

## Deliverables

### 1. `pyproject.toml`

- `dependencies`: fastapi, uvicorn, pydantic>=2, sqlalchemy[asyncio]>=2, asyncpg, alembic,
  httpx, anthropic, mcp, pyyaml, croniter, pyjwt[crypto], jsonschema.
- `[project.optional-dependencies] ml = ["transformers", "torch"]`.
- `[dependency-groups] dev`: keep the existing entries and add pytest-asyncio, aiosqlite,
  types-pyyaml, types-croniter, types-jsonschema.
- `[tool.hatch.build.targets.wheel] packages = ["src/telescreen", "src/telescreen_police_sdk"]`.
- `[tool.pytest.ini_options]`: `testpaths = ["tests"]`, `pythonpath = ["tools", "."]`,
  `addopts = "-ra --import-mode=importlib"`, `asyncio_mode = "auto"`,
  `asyncio_default_fixture_loop_scope = "function"`,
  `markers = ["integration: needs TELESCREEN_TEST_DATABASE_URL or TELESCREEN_TEST_MEILI_URL"]`.
- `[tool.ruff.lint.isort] known-first-party = ["telescreen", "telescreen_police_sdk", "typelint", "tests"]`.
- `[tool.ty.environment] extra-paths = ["tools", "."]`.
- `[tool.typelint.per-file-ignores]`: add an entry ONLY if task 3 proves it necessary for
  `src/telescreen/core/json.py`, and add the same entry for
  `src/telescreen_police_sdk/_json.py` (same boundary technique). Each entry needs a
  comment that names the boundary ("stdlib json / PyYAML return untyped natives"). Rules
  limited to `["TL005"]`.

### 2. `uv.lock`, `.venv`, `mise.toml`, `.gitignore`

- Run `uv lock` (no `--upgrade`), then `uv sync --dev`. Do not install the `ml` extra.
  Confirm `ty` is still 0.0.85 in `uv.lock`.
- `mise.toml`: add tasks `web-install` (`cd src/web && bun install --frozen-lockfile`),
  `web-typecheck`, `web-test`, `web-build` (`bun run typecheck`, `bun run test`,
  `bun run build` in `src/web`), `web-ci` (all four in order). Keep the existing tasks.
- `.gitignore`: add `/var/` (default evidence dir `./var/evidence`). `node_modules` and
  `src/web/dist` are already ignored; keep them.

### 3. `src/telescreen/core/` (L0; imports only stdlib, pydantic, pyyaml)

| File | Public contract (frozen for all plans) |
|------|----------------------------------------|
| `__init__.py` | docstring only |
| `vocabulary.py` | StrEnums listed below + `severity_for(rule_class: RuleClass) -> Severity` |
| `json.py` | `JsonKind`, `JsonValue`, `JsonMember`, `parse_json`, `parse_yaml`, `dump_json`, constructors, accessors (below) |
| `errors.py` | `class TelescreenError(Exception)` with fields `code: ErrorCode`, `detail: str`, `__init__(self, code: ErrorCode, detail: str)`; `class ConfigError(TelescreenError)` with `__init__(self, detail: str)` (code `config_error`) |
| `clock.py` | `Clock` Protocol `now() -> datetime`; `SystemClock`; `FakeClock(current: datetime)` with `now()`, `advance(delta: timedelta) -> None`, `set(value: datetime) -> None`; `ensure_utc(value: datetime) -> datetime` |
| `durations.py` | `parse_duration(text: str) -> timedelta` (ISO-8601 `PnW`, `PnDTnHnMnS`; reject `Y` and month `M` with `TelescreenError(validation_error)`) |
| `ids.py` | `new_id() -> str` (uuid4 string) |
| `secrets.py` | `SecretScheme` (`env`, `file`), `SecretRef(scheme, target)`, `parse_secret_ref(ref: str) -> SecretRef`, `SecretValue(value: str = field(repr=False))`, `resolve_secret(ref: str, env: EnvSource) -> SecretValue` |
| `config.py` | `EnvSource` Protocol `get(self, key: str, /) -> str | None`; `TelescreenSettings` frozen dataclass; `load_settings(env: EnvSource) -> TelescreenSettings` |

Vocabulary enums (member names upper case, values exactly as shown):

- `RuleClass`: credential, token, private_key, password, payment_card, national_id, email,
  phone, address. `Severity`: high, medium. `severity_for` maps the first six classes to
  high and the rest to medium.
- `DetectorKind`: rule, ml, managed_llm, local_llm, police. `DetectorMode`: union, cascade.
  `DlpPolicy`: mask, block. `ManagedLlmProvider`: claude, codex.
- `IssueStatus`: unresolved, acknowledged, in_progress, resolved, ignored, regressed.
  `IssueResolution`: manual, auto. `IssueHistoryKind`: created, status_changed, assigned,
  commented, regressed, auto_resolved, workflow_started, evidence_added, sla_breached.
  `IssueEventKind`: issue_created, issue_regressed.
- `CollectorKind`: slack, gdrive, gdocs, gsheets, github, custom, police. `TriggerMode`:
  event, schedule, both. `ScanTrigger`: event, schedule, push, manual, workflow, sweep.
  `ScanJobStatus`: running, succeeded, failed, partial.
- `ActorKind`: member, token, system, police, collector. `ApiScope`: read, write, admin,
  ingest. `SourceFormat`: yaml, json.
- `StepType`: detect, ai_agent, condition, human_task, notify, evidence_upload,
  police_verify, wait, send_back, parallel, join, sub_workflow, map, end.
  `RunStatus`: running, waiting, succeeded, failed, cancelled. `RunTrigger`:
  issue_created, issue_regressed, manual, sub_workflow. `StepRunStatus`: ready, running,
  waiting, succeeded, failed, cancelled. `TransitionKind`: start, next, branch, send_back,
  fork, join, timeout, error.
- `EngineTaskKind`: advance_step, timer_fired, sla_check, police_timeout, collector_scan,
  police_push_delivery, notification_delivery, child_run_finished, collector_watch_renew,
  map_partition, map_dispatch, map_shard, map_reduce. `EngineTaskStatus`: ready, leased,
  done, cancelled, dead.
- `HumanTaskKind`: approval, evidence. `HumanTaskStatus`: open, completed, cancelled.
- `NotificationChannel`: slack, email. `DlpVerdict`: pass, masked, blocked (member
  `PASS = "pass"`). `NotificationStatus`: queued, sent, failed, blocked.
- `EvidenceVerdict`: accepted, rejected.
- `PoliceMode`: pull, push. `PoliceWorkerStatus`: active, disabled. `PoliceTaskStatus`:
  queued, leased, delivered, completed, failed, expired, cancelled. `PoliceOutcome`:
  passed, failed.
- `MapOrigin`: workflow, collector. `MapJobStatus`: partitioning, running, reducing,
  succeeded, partial, failed, cancelled. `MapPartitionStatus`: ready, leased, done,
  failed. `MapItemStatus`: ready, dispatched, succeeded, failed, cancelled.
- `ErrorCode`: validation_error, unauthorized, forbidden, not_found, conflict,
  raw_value_detected, evidence_rejected, evidence_required, unsupported_media_type,
  evidence_scanner_unavailable, payload_too_large, invalid_transition, internal_error,
  config_error, unknown_source, unsupported_contract_version, schema_invalid,
  signature_invalid, replay_detected, token_invalid, task_not_leased, deadline_passed,
  sweep_unsupported, sweep_timeout, rate_limited, fetch_failed, too_large,
  malformed_image, task_dead, loop_limit_exceeded, expression_error, workflow_invalid,
  not_rescannable, llm_output_invalid, llm_unavailable, detector_unavailable,
  provider_error, delivery_failed, recipient_unknown, chain_broken, index_guard_rejected.

`JsonValue` shape (the self-referencing frozen dataclass, never a `type` alias):

```python
@dataclass(frozen=True)
class JsonValue:
    kind: JsonKind                       # null, bool, int, float, string, array, object
    bool_value: bool = False
    int_value: int = 0
    float_value: float = 0.0
    str_value: str = ""
    items: tuple[JsonValue, ...] = ()
    members: tuple[JsonMember, ...] = ()
```

- Constructors (module functions): `json_null()`, `json_bool(v)`, `json_int(v)`,
  `json_float(v)`, `json_str(v)`, `json_array(items: Sequence[JsonValue])`,
  `json_object(members: Sequence[tuple[str, JsonValue]])`. Duplicate member names raise
  `TelescreenError(validation_error)`.
- Accessors (methods): `get(name: str) -> JsonValue | None`, `as_str() -> str | None`,
  `as_int() -> int | None`, `as_float() -> float | None` (accepts int), `as_bool() -> bool | None`,
  `as_list() -> tuple[JsonValue, ...] | None`, `is_null() -> bool`, `member_names() -> tuple[str, ...]`.
- `parse_json(text: str | bytes) -> JsonValue`; `parse_yaml(text: str) -> JsonValue` via
  `yaml.safe_load`, converting YAML dates and datetimes to ISO-8601 strings and rejecting
  other non-JSON types; `dump_json(value: JsonValue) -> str` writes canonical JSON
  (sorted keys, separators `(",", ":")`, `ensure_ascii=False`).
- Pitfalls: check `bool` before `int` (Python bools are ints); reject NaN and Infinity;
  invalid input raises `TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid JSON document")`,
  and the detail must never contain the input; YAML anchors and aliases are allowed but a
  non-string mapping key is rejected.
- Native conversion: use structural Protocols in the style of `tools/typelint.py:TomlTable`.
  If ty cannot narrow them, use the pre-approved TL005 per-file-ignore (deliverable 1) and
  record that decision in the Progress Log.

`TelescreenSettings` has one field per variable in `design-docs/specs/command.md`
(Environment Variables). Names are snake_case without the prefix. Secret refs keep the
`_ref` suffix (for example `fingerprint_key_ref: str | None`); lists are tuples of enums.
Defaults are as documented. Pitfalls:

- `managed_llm_model` resolves to `claude-sonnet-5-5` when the provider is `claude` and
  the variable is unset. Provider `codex` without a model raises `ConfigError`.
- Booleans accept `true/false/1/0`, case-insensitive.
- Every invalid value raises `ConfigError` with a static detail naming the variable and
  never its value.
- Required-ness is NOT enforced in `load_settings` (`workflow validate` needs no database).
  Consumers raise `ConfigError` themselves.

### 4. Police SDK package shell

- `src/telescreen_police_sdk/__init__.py`: docstring plus `__version__: str = "0.1.0"`.
- `src/telescreen_police_sdk/py.typed`: empty file.
- This lets `uv build` include the package. Plan p11 owns every other SDK file.

### 5. Test support

- `tests/conftest.py`: skip `integration`-marked tests unless
  `TELESCREEN_TEST_DATABASE_URL` or `TELESCREEN_TEST_MEILI_URL` is set.
- `tests/support/__init__.py`: `require_env(name: str) -> str` (calls `pytest.skip` when
  unset).
- `tests/support/samples.py`: `Sample(name: str, rule_class: RuleClass, value: str = field(repr=False))`.
  - One function per sample, each assembled from string fragments at runtime so gitleaks
    sees no literal: `aws_access_key_id`, `aws_secret_access_key`, `github_token`,
    `slack_token`, `google_api_key`, `pem_private_key`, `jwt_token`,
    `password_assignment`, `credit_card` (Luhn-valid, Visa IIN), `my_number` (valid check
    digit), `email_address`, `jp_mobile`, `jp_landline`, `jp_address`.
  - `all_samples() -> tuple[Sample, ...]`.
  - `separator_stripped(value: str) -> str` removes spaces and hyphens.
  - `find_leaks(text: str) -> tuple[str, ...]` returns the NAMES of samples found as the
    value or its separator-stripped variant.
  - `assert_no_leaks(text: str, where: str) -> None` fails with the names and `where`, but
    never the values.

### 6. Third-party typing spike (`tests/core/test_third_party_typing.py`)

A real test module that must pass ruff, typelint and ty. It proves that these patterns
compile under the strict rules:

- a FastAPI route with a `pydantic.dataclasses.dataclass` body and response, called
  through `httpx.ASGITransport`;
- a SQLAlchemy `class Base(MappedAsDataclass, DeclarativeBase)` model with
  unannotated `__tablename__ = "..."` and `__table_args__ = (...)` (typelint exempts
  class-body dunders; ty 0.0.85 rejects `ClassVar[str]` as an invalid override and treats
  `__tablename__: str` as a dataclass field), written and read on aiosqlite in memory;
- `mcp.server.fastmcp.FastMCP` registering one typed tool function (not run);
- `anthropic.AsyncAnthropic(api_key="x", http_client=httpx.AsyncClient(transport=MockTransport(...)))`
  constructed (no call needed).

If one of these needs a per-file-ignore, STOP and record a Reconciliation request with
the exact ty or typelint message. Do not add an ignore for it.

### 7. `README.md` (final)

Cover: purpose; architecture overview (processes, data flow, the naming map with only
Telescreen, Reporting Bureau and Police themed); data-handling guarantees; setup
(`mise run setup`); configuration (point to `design-docs/specs/command.md`); CLI usage
(command table); API overview; UI dev and build (`mise run web-ci`); Police (pull, push,
SDK, n8n paths `examples/police/`); compose quick start (`examples/compose/`); testing
(`mise run ci`, integration env vars). English, no emojis. Later plans never edit it.

### 8. `src/web/package.json` and `src/web/bun.lock`

- `name: "telescreen-web"`, `private: true`, `type: "module"`.
- scripts: `dev: vite`, `build: vite build`, `typecheck: tsc --noEmit`,
  `test: vitest run`.
- dependencies: solid-js, @solidjs/router.
- devDependencies: typescript, vite, vite-plugin-solid, vitest, jsdom,
  @solidjs/testing-library, @testing-library/jest-dom.
- Run `cd src/web && bun install` to create `bun.lock` and `node_modules`.

### 9. Toolchain audit manifest (tracked; section "Toolchain Audit Manifest" below)

The artifact roots `.venv` and `src/web/node_modules` are recorded as digests only, so
the toolchain that produced them must be recorded in this tracked plan file. After the
installs, fill the table in the "Toolchain Audit Manifest" section with real values:

- Versions: `python --version`, `uv --version`, `bun --version`, `node --version`, plus
  the `ty` and `ruff` versions read from `uv.lock`.
- Provenance per tool: mise-managed (`mise.toml`), `uv.lock`, or `bun.lock`.
- The exact install commands (`mise install`, `uv lock`, `uv sync --dev`,
  `cd src/web && bun install`) with their exit codes and `/tmp` log paths.
- `shasum -a 256 uv.lock src/web/bun.lock` after the install.
- Pin `uv` and `bun` in `mise.toml` `[tools]` to the exact recorded versions (they are
  `latest` today) so later reruns reproduce the same toolchain. Leave `python = "3.12"`
  and `node = "22"` as they are.

## Tests (`tests/core/`)

- `{"a":[1,true,null,1.5,"x"]}` parsed, then dumped -> identical canonical text; bool is
  not int.
- `parse_json("{bad")` -> `TelescreenError` code `validation_error`; `str(err)` does not
  contain `bad`.
- YAML with a date value -> ISO string; YAML with an integer key -> validation_error.
- `dump_json` sorts keys; `json_object` with duplicate names -> validation_error.
- `parse_duration("PT4H")` -> 4 h; `"P1D"`, `"P2W"`, `"PT15M"` parse; `"P1Y"`, `"P1M"` and
  `"garbage"` -> validation_error.
- `load_settings` on a fake env: defaults; provider codex without model -> ConfigError;
  `TELESCREEN_WORKER_CONCURRENCY=abc` -> ConfigError whose message lacks `abc`;
  `TELESCREEN_DETECTORS=rule,managed_llm` -> tuple of two enums.
- `resolve_secret("env:X")` with X set -> value; `repr()` of the result lacks the value;
  `file:` reads and strips the trailing newline; an unknown scheme -> ConfigError.
- `FakeClock.advance` works; `ensure_utc(naive)` -> tzinfo UTC.
- `severity_for` covers every `RuleClass`.
- samples: the Luhn check passes for `credit_card()`, the My Number check digit is valid
  (re-implement both in the test); `find_leaks` finds the separator-stripped variants;
  `repr(Sample)` lacks the value.
- Third-party typing spike as described.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
uv lock && uv sync --dev > /tmp/telescreen-p01-uv-lock-sync-final.log 2>&1
grep -A1 'name = "ty"' uv.lock | grep -q 'version = "0.0.85"'
uv run --frozen ruff format --check --no-cache src/telescreen/core src/telescreen_police_sdk tests/core tests/support tests/conftest.py > /tmp/telescreen-p01-format-final.log 2>&1
uv run --frozen ruff check --no-cache . > /tmp/telescreen-p01-ruff-final.log 2>&1
uv run --frozen python tools/typelint.py > /tmp/telescreen-p01-typelint-final.log 2>&1
uv run --frozen ty check > /tmp/telescreen-p01-ty-final.log 2>&1
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-p01-pytest-final.log 2>&1
uv build --out-dir /tmp/telescreen-p01-foundation-dist-final > /tmp/telescreen-p01-build-final.log 2>&1
unzip -l /tmp/telescreen-p01-foundation-dist-final/*.whl | grep -E 'telescreen_police_sdk/__init__.py|telescreen/core/json.py' > /tmp/telescreen-p01-wheel-contents.log 2>&1
cd src/web && bun install --frozen-lockfile > /tmp/telescreen-p01-bun-frozen.log 2>&1
test "$(grep -c '^| .*TBD' impl-plans/active/p01-foundation.md)" -eq 0
```

Because this is wave 1, full-repository checks must pass, not just scoped ones.

## Completion Criteria

- [x] All deliverables exist with the exact public names above
- [x] `ty` pin unchanged (0.0.85); no new per-file-ignores except the pre-approved JSON
      boundary, if proven necessary and logged
- [x] All verification commands exit 0 with log paths recorded
- [x] README covers every topic in deliverable 7
- [x] The Toolchain Audit Manifest table has real values in every cell
      (`grep -c '^| .*TBD' impl-plans/active/p01-foundation.md` prints 0); `uv` and
      `bun` are pinned in `mise.toml`
- [x] Progress Log updated with hashes and command evidence

## Toolchain Audit Manifest

| Item | Version or digest | Provenance | Install command | Exit | Log |
|------|-------------------|------------|-----------------|------|-----|
| python | 3.12.14 | mise (`python = "3.12"`) | `mise install` | 0 | `/tmp/telescreen-p01-mise-install.log` |
| uv | 0.12.3 | mise (`uv = "0.12.3"`) | `mise install` | 0 | `/tmp/telescreen-p01-mise-install.log` |
| bun | 1.4.2 | mise (`bun = "1.4.2"`) | `mise install` | 0 | `/tmp/telescreen-p01-mise-install.log` |
| node | v22.23.2 | mise (`node = "22"`) | `mise install` | 0 | `/tmp/telescreen-p01-mise-install.log` |
| ty | 0.0.85 | uv.lock | `uv sync --dev` | 0 | `/tmp/telescreen-p01-uv-lock-sync-final.log` |
| ruff | 0.16.10 | uv.lock | `uv sync --dev` | 0 | `/tmp/telescreen-p01-uv-lock-sync-final.log` |
| uv.lock sha256 | `1f24a7a12cb37b7f753c143b5fbf94257a01d10012db1e037d7c05734a8e3bc2` | generated | `uv lock` | 0 | `/tmp/telescreen-p01-toolchain-details.log` |
| .venv | 62 installed package entries | uv.lock | `uv sync --dev` | 0 | `/tmp/telescreen-p01-toolchain-details.log` |
| src/web/bun.lock sha256 | `125174f45a6fceb669b0c86a3ceea99a920046071a318c156f0993580f3f6e45` | generated | `cd src/web && bun install` | 0 | `/tmp/telescreen-p01-toolchain-details.log` |
| src/web/node_modules | 121 top-level entries | bun.lock | `cd src/web && bun install` | 0 | `/tmp/telescreen-p01-toolchain-details.log` |

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-08 19:45 JST
**Tasks Completed**: Implemented the p01 dependency/toolchain foundation; core vocabulary, JSON/YAML, errors, clock, durations, IDs, secrets, and settings modules; standalone Police SDK shell; integration-test support and synthetic samples; third-party typing spike; README; web package manifest and lock. Updated audit manifest and completion criteria.
**Tasks In Progress**: None
**Blockers**: None
**Reconciliation requests**: None. The JSON native decoder requires the pre-approved narrow `TL005` boundary entry for stdlib json / PyYAML untyped native values; the mirrored SDK entry is retained for p11's same boundary.
**Hashes**: `pyproject.toml` before=d22f380e67be878c517cd67d7efb1387cfd11da9184ca2d65614b74e0e2f2118 after=42b939e0c442981f7d556979b87d8014ad98a8da170e287208142eceb8a416cb; `uv.lock` before=39da26cb0587e00dc17f2583e46b0310688ab48f6358e00f1a29a1d4f17c7d59 after=1f24a7a12cb37b7f753c143b5fbf94257a01d10012db1e037d7c05734a8e3bc2; `mise.toml` before=0e174b7ab47030eb4c915adec64639e0e111cc7e04ce8b2e6744e08b2dbe6d9a after=12c8845296438d1187f9ad258c436be839ebfd7665b6d4574e33f07f09d5fdfb; `.gitignore` before=d19b85920ff82150d02472f5981df51f80fdf432375002f8c1fe8ecaca2e3353 after=1b03881a90cb58f0b420d952558ecaa699489c5554bcbbf3516f1b6f20ce5d31; `README.md` before=aa5f1c6ca6c3ffa348aac70659dfd9981e59bec1f2beab3abcbf858d8cb53246 after=a63b2947ac05d1d86dc501cd63e50020af8c3cdd19603df4e921622c43777c1c; new files hashes recorded in task evidence.
**Verification**: `mise install` -> exit 0 (log: `/tmp/telescreen-p01-mise-install.log`); `uv lock && uv sync --dev` -> exit 0 (log: `/tmp/telescreen-p01-uv-lock-sync-final.log`); `cd src/web && bun install` -> exit 0 (log: `/tmp/telescreen-p01-bun-install.log`); scoped Ruff format check -> exit 0 (log: `/tmp/telescreen-p01-format-final.log`); full-repository Ruff -> exit 0 (log: `/tmp/telescreen-p01-ruff-final.log`); typelint -> exit 0 (log: `/tmp/telescreen-p01-typelint-final.log`); ty -> exit 0 (log: `/tmp/telescreen-p01-ty-final.log`); full pytest -> exit 0, 47 passed (log: `/tmp/telescreen-p01-pytest-final.log`); wheel build -> exit 0 (log: `/tmp/telescreen-p01-build-final.log`); wheel contents -> exit 0 (log: `/tmp/telescreen-p01-wheel-contents.log`); SDK files in wheel -> exit 0 (log: `/tmp/telescreen-p01-wheel-sdk.log`); frozen bun install -> exit 0 (log: `/tmp/telescreen-p01-bun-frozen.log`); no-TBD manifest gate -> exit 0 (log: `/tmp/telescreen-p01-no-tbd-final.log`).
**Notes**: First verification pass identified formatting, annotations, and incompatible latest SDK major versions. Pinned Anthropic `<1`, MCP `<2`, and SQLAlchemy `<2.1` to match the accepted SDK surfaces; corrected issues and reran all relevant gates successfully. Initial logs: `/tmp/telescreen-p01-format.log`, `/tmp/telescreen-p01-ruff.log`, `/tmp/telescreen-p01-typelint.log`, `/tmp/telescreen-p01-ty.log`, and `/tmp/telescreen-p01-pytest.log`; their reported issues were corrected. No code outside p01 writePaths was changed; existing `src/telescreen` scaffold files were preserved.
