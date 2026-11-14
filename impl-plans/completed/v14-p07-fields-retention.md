# V14-P07 Field Schema, Values, Registration and Retention

**Status**: Completed
**Design Reference**: design-docs/specs/design-v1-4-data-inventory.md sections 2 (D14-D18), 7, 8, 9, 11, 12
**Created**: 2026-10-13
**Last Updated**: 2026-10-13

## Plan Metadata

- planId: `v14-p07-fields-retention`
- planPath: `impl-plans/active/v14-p07-fields-retention.md`
- wave: 3
- dependsOn: `v14-p04-inventory-core`
- writePaths:
  - `impl-plans/active/v14-p07-fields-retention.md`
  - `src/telescreen/inventory/fields.py`
  - `src/telescreen/inventory/values.py`
  - `src/telescreen/inventory/registration.py`
  - `src/telescreen/inventory/retention.py`
  - `src/telescreen/issues/asset_owner.py`
  - `src/telescreen/api/routers/data_fields.py`
  - `src/telescreen/api/routers/data_asset_values.py`
  - `tests/inventory/fields`
  - `tests/inventory/retention`
  - `tests/issues/v14_asset_owner`
  - `tests/api/v14_data_fields`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first. p04's modules (`inventory/repository.py`,
`lifecycle.py`, `queries.py`, `api/routers/data_common.py`) are read-only here. A defect
there is a Reconciliation request naming p04.

## Intent and Context

This plan implements, all audited:

- the admin field schema (R5);
- validated per-asset values with required enforcement and `metadata_changed` events (R6);
- manual registration;
- the overdue evaluation with one notification per due date (R7);
- the `AssetOwnerResolver` port in the issues package (C6).

The routers are tested by building them directly; p08 registers them and binds the
provider and handler.

Imitate:

- `issues/views.py:213-219` (the `Redactor` reject pattern);
- `issues/external/links.py:54-57` (https URL and no-userinfo validation);
- `issues/ownership.py:31-67` (`match_rule`) and `216-266` (owner resolution order);
- `notifications/weekly.py:WeeklyReportProvider` and `WeeklyReportHandler` (cron ->
  idempotent task -> handler);
- `alerts/evaluator.py:204-280` (a templated `NotificationRequest` with `event`,
  `template_name`, `context` and `record_unreachable`);
- `issues/activity/mentions.py:73` (`send_to_member`);
- `api/routers/ownership.py` (reorder route ordering and the router deps style);
- p04's `InventoryRepository.append_event` and `data_common.asset_detail_json`.

## Non-goals

- No changes to p04 files, notifications, directory or wiring. No new notification
  category (D18). No LLM detector use (D14).

## Invariants

- No metadata value, label, name, locator or note is echoed in an error, logged, or
  written to audit details. Audit details hold ids, enums, counts and field keys only.
- Field keys are immutable. A type change or hard delete happens only with zero values.
- An overdue asset is notified at most once per due date (`overdue_notified_for`).
- All values are written atomically per request.

## Deliverables

1. `inventory/fields.py`: `FieldService(audit: AuditLog, redactor: Redactor, clock:
   Clock)` with `list`, `create`, `update`, `archive`, `unarchive`, `delete` and
   `reorder`, implementing design 7.1 exactly:
   - key pattern and uniqueness, including archived fields (409 `conflict`);
   - label and description lengths, with `Redactor` checks;
   - options required for select types and forbidden otherwise; option value pattern,
     uniqueness and label `Redactor` check;
   - role and type compatibility; one non-archived field per role (409 `conflict`), with
     the same check on unarchive;
   - at most 100 fields;
   - a new field gets `position = max + 1`; reorder requires the exact set of
     non-archived ids (422 otherwise);
   - a type change with values, an option removal that a value uses, and a delete with
     values each give 409 `field_in_use`;
   - `value_count` per field for the responses.

   Audit `data_field.created`, `updated`, `reordered`, `archived`, `unarchived` and
   `deleted`. `options_json` is the `dump_json` of `[{value, label}]`.
2. `inventory/values.py`:
   - `ValueValidator(recipients: RecipientResolver, redactor)`
     with `validate(session, fields, values) -> tuple[ValueIssue, ...]`, where
     `ValueIssue(field, code)` uses the codes of design 7.2. It parses `value_json` with
     `telescreen.core.json.parse_json` and checks the type table rules.
     - `reference`: call `RecipientResolver.assignee_of(ref)`. It already rejects
       inactive members and missing teams by raising `TelescreenError(RECIPIENT_UNKNOWN)`
       (`directory/resolver.py:83-97`). Catch exactly that code and report
       `unknown_reference`, which becomes 422 `invalid_field_value` on `PUT` and an entry
       in `validate-values`; it must never propagate (it is unmapped, so it would be a
       500). A malformed ref (no `member:` or `team:` prefix) is also
       `unknown_reference`. No extra `ActorDirectory` check is needed.
     - Every string leaf goes through `Redactor.has_hits`, giving `raw_value_detected`.
   - `canonical_value(field, value) -> (value_json, value_date | None)` and
     `display_value(field_type, value_json) -> str` (at most 500 characters; multi-select
     joined with `, `).
   - `ValueService(repository: InventoryRepository, validator, audit, clock)` with
     `put(session, asset, fields, values, actor_ref) -> None`:
     - validate, apply, then run the required check over the resulting state;
     - on the first failing rule raise `TelescreenError` with the matching code
       (`raw_value_detected`, `invalid_field_value` or `required_field_missing`) and a
       static detail;
     - write one `metadata_changed` event per changed field (`field_key`, old and new
       display text) through `append_event`;
     - write one audit record `data_asset.fields_changed` listing the field keys.
3. `inventory/registration.py`: `RegistrationService(repository, values: ValueService,
   fingerprinter: Fingerprinter, redactor, audit, clock)` with `register(session, body,
   actor_ref) -> str` (the asset id), implementing design 7.4:
   - `resource_key = fingerprinter.resource_key("registered:" + source, locator)`;
   - a duplicate is 409 `conflict`. First check `repository.get_by_resource_key`; if
     that finds an asset, return 409. Then insert inside `begin_nested`. If the insert
     raises `IntegrityError`, which means a concurrent registration won the race, also
     return 409. Do not use `insert_asset`'s re-read-and-continue path here; that
     behavior belongs to the observer;
   - `display_locator` is the locator, which is already `Redactor`-checked;
   - `origin = registered`, `state = present`, `first_seen_at = now`, `created_by`;
   - one `registered` event (with `item_count_after` when given), then the values through
     `ValueService` with required enforced;
   - audit `data_asset.registered`.
4. `issues/asset_owner.py`: `IssueAssetOwnerResolver(owners: OwnerDirectory)` implements
   `AssetOwnerResolver.fallback_owner`:
   - first, open issues (`RECONCILED_STATUSES`) with that `resource_key` and a non-null
     `owner_ref` -> `owners.member_for_owner(owner_ref)`, normalized to `member:<handle>`.
     Read `directory/owners.py:48-70` for the return format; use
     `OwnerDirectory.member_ref` if it returns an id;
   - then the first enabled ownership rule from `match_rule(rules, source,
     display_locator)` -> `team:<slug>`;
   - otherwise None.

   Registered assets have no issues, so only the rules can match.
5. `inventory/retention.py`:
   - `DataOverdueProvider(cron: str, queue: TaskQueue, clock: Clock)`, a
     `ScheduleProvider`: when the cron fires, enqueue `EngineTaskKind.DATA_OVERDUE_CHECK`
     with idempotency key `data-overdue:<fire minute ISO>`. An empty cron means it never
     fires.
   - `DataOverdueHandler(sessions, repository, members: MemberNotifier, sender:
     NotificationSender, recipients: RecipientResolver, owners: AssetOwnerResolver, audit,
     clock, public_base_url: str | None)`, a `TaskHandler` of that kind.

     It iterates by keyset, never by re-running the predicate from the start:
     - order by `data_assets.id`; each batch selects overdue assets (D16, the same
       predicate as p04's overdue tab) whose `overdue_notified_for` is null or differs
       from the due date, with `id > last_id`, limit 200;
     - `last_id` advances past every selected asset, whatever its outcome;
     - the run ends when a batch is empty, so each asset is attempted at most once per
       run.

     Each asset is handled in its own `sessions.begin()`:
     1. Resolve the recipient (design 8.2):
        - the owner role value, validated with `assignee_of` (a member gets its
          `member_id`);
        - if `assignee_of` raises `TelescreenError` with code `RECIPIENT_UNKNOWN` (a
          deactivated member or a deleted team) or the ref is malformed, treat it as "no
          owner value" and continue to `fallback_owner`, resolved the same way;
        - otherwise none.
     2. Send. A member goes through `send_to_member(MemberNotification(member_id,
        NotificationCategory.ASSIGNED, NotificationEvent.DATA_OVERDUE, context))`. A team
        gets two `sender.send(...)` calls, email and Slack, with `template_name=
        "system.data_overdue"` and `record_unreachable=True`.
     3. Set `overdue_notified_for = due`.
     4. Append audit `data_asset.overdue_notified` (`asset_id`, `recipient_kind`,
        `notification_count`).

     The context fields are `data_short_id`, `data_name`, `data_locator`,
     `data_deletion_due` and `data_link` (`public_base_url + "/data/" + id`, or None when
     the base URL is unset). With no `deletion_due` field the handler returns done
     immediately.
6. Routers, each route with exactly one permission:
   - `data_fields.py`: `DataFieldsRouterDeps(ctx, fields: FieldService, validator:
     ValueValidator)` and `build_data_fields_router(deps)`:
     - `GET /api/data/fields` (`DATA_READ`);
     - `POST /api/data/fields/validate-values` (`DATA_WRITE`) ->
       `{valid, errors: [{field, code}]}`, with 200 even when invalid;
     - `POST /api/data/fields/reorder` (`DATA_FIELDS_ADMIN`).

     These three are registered before the `{field_id}` routes. Then, all
     `DATA_FIELDS_ADMIN`:
     - `POST /api/data/fields`;
     - `PATCH /api/data/fields/{field_id}`;
     - `DELETE /api/data/fields/{field_id}` (204);
     - `POST /api/data/fields/{field_id}/archive` and `.../unarchive`.
   - `data_asset_values.py`: `DataAssetValuesRouterDeps(ctx, values: ValueService,
     registration: RegistrationService, queries: AssetQueries)` and
     `build_data_asset_values_router(deps)`:
     - `PUT /api/data/assets/{asset_id}/fields` (`DATA_WRITE`) -> `asset_detail_json`;
     - `POST /api/data/assets` (`DATA_WRITE`) -> 201 `asset_detail_json`.

## Pitfalls

- `decimal` stays a string end to end. Never convert it to a float.
- `integer` values must be JSON integers; a JSON string `"5"` is `invalid_type`. `true`
  is not an integer.
- An archived field supplied in `PUT` is 422 (`archived_field`), but its stored values
  stay untouched and do not count for the required check.
- The required check runs over the resulting state, so clearing a required field is
  rejected even when other fields are valid.
- `validate-values` must never return the value. `errors` carries only the field key and
  the code.
- Notification sends happen inside the per-asset transaction. An unexpected failure rolls
  back that asset only: log the code (masked) and continue with the next id. The asset is
  not marked, but the keyset cursor has passed it, so it is not retried in this run; the
  next cron fire retries it. A stale owner ref (`RECIPIENT_UNKNOWN`) is not a failure: it
  falls through to the fallback, and the asset is marked and audited.
- Never loop by re-selecting "not yet marked" from the start. An asset that keeps failing
  would then never let the handler terminate.
- Team recipients: two sends, not one with both channels.

## Tests

- `tests/inventory/fields`:
  - key pattern and immutability (PATCH has no key);
  - a duplicate key that is archived -> 409;
  - select without options -> 422;
  - role `owner` on a `text` field -> 422; a second `deletion_due` -> 409;
  - a type change with one value -> 409, and without values -> ok;
  - removing an option in use -> 409;
  - delete with values -> 409;
  - reorder with a missing id -> 422;
  - archive hides the field from the required set;
  - unarchive with a role conflict -> 409;
  - a label holding a `tests.support.samples` secret -> 422, the message does not
    contain it, and caplog does not contain it.
- `tests/inventory/fields/test_values.py`:
  - every type's valid and invalid cases from design 7.2 (decimal pattern, the date
    `2026-02-30` is invalid, a multi-select duplicate is invalid, a URL with userinfo is
    invalid, an `http` URL is invalid);
  - an unknown member ref -> `unknown_reference`;
  - a deactivated member ref and a deleted team ref -> `unknown_reference`; `PUT`
    returns 422 `invalid_field_value`, never 500;
  - a secret in `long_text` -> `raw_value_detected` with no echo;
  - a missing required field -> `required_field_missing`;
  - a change writes `metadata_changed` events with display text and one audit record;
  - an unchanged value writes no event.
- `tests/inventory/fields/test_registration.py`:
  - success -> `DATA-n`, a `registered` event, values, audit;
  - a duplicate source and locator -> 409;
  - a simulated insert race (`IntegrityError` on insert after the pre-check passed) ->
    409, not 201 and not 500;
  - a secret in the name -> 422;
  - required fields enforced.
- `tests/inventory/retention`:
  - `FakeClock` before the due date -> not overdue, no send;
  - after it -> exactly one member send with category `assigned`, and the mark set;
  - a second run -> no send;
  - a changed due date -> sends again;
  - a deleted asset -> no send; an `unknown` asset -> sends;
  - a team owner -> two sends (email and Slack);
  - fallback through `fallback_owner` -> a team;
  - no recipient -> marked, audit `recipient_kind = none`;
  - the owner value references a deactivated member: with a fallback team rule -> two
    team sends and marked; with no fallback -> `recipient_kind = none` and marked. The
    handler returns `done` in both cases;
  - injected per-asset send exception (fake sender raises for asset A of 3) -> A is
    attempted exactly once in the run and is not marked, B and C are notified and marked,
    and the handler returns `done`;
  - more than 200 overdue assets -> all are processed across batches, and each is
    attempted once;
  - the provider enqueues once per fire minute, and an empty cron never enqueues.
- `tests/issues/v14_asset_owner`:
  - the open issue owner wins over the rules;
  - the rules match on source plus glob;
  - no match -> None;
  - a resolved issue's owner is ignored.
- `tests/api/v14_data_fields`:
  - every route's permission (viewer 403 on admin and write routes);
  - the route order: `POST /api/data/fields/reorder` is not captured by `{field_id}`;
  - `validate-values` returns 200 `{valid: false}` with codes;
  - `PUT` returns the detail with the new `value_json`;
  - register returns 201;
  - response key sets match design 9.1 (`DataField`, `DataAssetDetail`).

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`. `<src>` is
the Python files in writePaths. `<tests>` is
`tests/inventory tests/issues/v14_asset_owner tests/api/v14_data_fields tests/api/v14_data_assets`.

```bash
uv run --frozen ruff format --check --no-cache <src> <tests>
uv run --frozen ruff check --no-cache <src> <tests>
uv run --frozen ty check <src> <tests>
uv run --frozen python tools/typelint.py > /tmp/telescreen-v14-p07-typelint.log 2>&1; grep -E '^(src/telescreen/inventory/(fields|values|registration|retention)|src/telescreen/issues/asset_owner|src/telescreen/api/routers/data_(fields|asset_values)|tests/inventory/(fields|retention)|tests/issues/v14_asset_owner|tests/api/v14_data_fields)' /tmp/telescreen-v14-p07-typelint.log
uv run --frozen pytest -p no:cacheprovider <tests> 2>&1 | tee /tmp/telescreen-v14-p07-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v14-p07-pytest-all.log 2>&1; echo "exit=$?"
```

Expected: every command exits 0 and the typelint grep is empty. `<tests>` also re-runs
p04's suites, to prove they still pass.

## Completion Criteria

- [x] The six deliverables are done and every test case above passes.
- [x] No file exceeds 1000 lines.
- [x] The Progress Log has hashes, and every command with its exit status and log path.

## Progress Log

Completed p07 field schema, value validation and writes, manual registration, issue asset
owner fallback, overdue scheduling/handling, and the two unregistered API routers. Added
service, retention, resolver, route-order/permission and API behavior tests. The API
permission test double now raises for missing role permissions instead of replacing the
guard with an unchecked principal. Registration's unique-key insert race is covered by a
database-backed conflict test. Routers remain unregistered for p08 as planned.

Initial p07 completion source and test SHA-256 (before P07-TI-1):

| Path | SHA-256 |
| --- | --- |
| `src/telescreen/inventory/fields.py` | `2948c38876ff71ad3fbf5a0616ec1e38fda3e9243754ffbd8ecf66449cd5a8b4` |
| `src/telescreen/inventory/values.py` | `082b6d89352309b29401622dc99eeccdab420426edb4e71b61441f0a097c374b` |
| `src/telescreen/inventory/registration.py` | `407c8e950f2fa6ad66b8733e429a751bd15608e00546470c9d0c22026587b901` |
| `src/telescreen/inventory/retention.py` | `b87fc9eaf9d542748732926920f53f5ea24dc89604f79aeb21151a8a351a0bf1` |
| `src/telescreen/issues/asset_owner.py` | `9635b1df7bb0bbda535c7983177f0b6c2acac1e14f9d1c2f944fbc3887860e26` |
| `src/telescreen/api/routers/data_fields.py` | `8d65dec5a5b28464bb06f9086d39b95c8527586fa6eb7aad6ce452cdd555d1d2` |
| `src/telescreen/api/routers/data_asset_values.py` | `f70b1e3f32c8adcc6662f9198958e6cab14400c1662e01c814256aa9177b9d09` |
| `tests/inventory/fields/test_registration.py` | `9dbbb98963576bdc76f6e5b36722d5926c1ff2def2698691e791429af660efea` |
| `tests/inventory/fields/test_schema.py` | `edc1f75bc22b20a4632265bd18c05700ab1a533ec5bfc17996ffcd028c25bd94` |
| `tests/inventory/fields/test_values.py` | `3e1bb548df9477b3a4a886da55ce240e7852e71f6817d3af071f3826f29861e3` |
| `tests/inventory/retention/test_handler.py` | `6c16811d775e46db2c45777730c6f92fac0e211646e3e416c6fd405d827f1620` |
| `tests/issues/v14_asset_owner/test_resolver.py` | `1c74e946a909da6af153d4aa07f23dfd88d668eb518f24dea61fffa3ad3fcce1` |
| `tests/api/v14_data_fields/test_routes.py` | `d62603cff7b1c87e79af600cf98e5c9184b263a832d958c9b88162dc9a35b718` |

Initial p07 completion verification (all commands exited 0; superseded for the retention test file by P07-TI-1 below):

- `uv run --frozen ruff format --check --no-cache <p07 source and test paths>`: 22 files
  formatted; `/tmp/telescreen-v14-p07-format-final.log`.
- `uv run --frozen ruff check --no-cache <p07 source and test paths>`: all checks passed;
  `/tmp/telescreen-v14-p07-ruff-final.log`.
- `uv run --frozen ty check <p07 source and test paths>`: all checks passed;
  `/tmp/telescreen-v14-p07-ty-final.log`.
- `uv run --frozen python tools/typelint.py` with the p07 path filter: zero p07
  violations; whole-tree tool exit 0; `/tmp/telescreen-v14-p07-typelint-final.log`.
- `uv run --frozen pytest -p no:cacheprovider tests/inventory tests/issues/v14_asset_owner tests/api/v14_data_fields tests/api/v14_data_assets`:
  42 passed; `/tmp/telescreen-v14-p07-pytest-final.log`.
- `uv run --frozen pytest -p no:cacheprovider`: 1090 passed, 11 environment-gated
  skips; `/tmp/telescreen-v14-p07-pytest-all-final.log`.
- `uv run --frozen pytest -p no:cacheprovider tests/inventory/fields/test_registration.py`:
  2 passed, including the IntegrityError race; `/tmp/telescreen-v14-p07-registration-race.log`.
- `wc -l` over all p07 Python source/test files: maximum 385 lines; no file reaches 1000;
  `/tmp/telescreen-v14-p07-wc-final.log`.

Plan file SHA-256 before this progress update: `520695a8ec93b3150537befb976695260bb5e698d763d8d3b20ff1cb6eb99dd3`.

### P07-TI-1 repair

Preserved the original four retention tests and added five tests in
`tests/inventory/retention/test_handler.py`: isolated three-asset send failure and next-run
retry; changed due-date resend; future-date exclusion; deleted versus unknown state; and
no-recipient audit with `recipient_kind=none`, `notification_count=0`, and a persisted mark.
All nine retention tests pass. Test file SHA-256 before/after: `6c16811d775e46db2c45777730c6f92fac0e211646e3e416c6fd405d827f1620` ->
`265425803762d1da5f6e793dc18b9dd1b2b86899c5440817446c4e04b57fc822`.

Final-source verification (all exit 0):

- `uv run --frozen pytest -p no:cacheprovider tests/inventory/retention -v`: 9 passed;
  `/tmp/telescreen-v14-p07-ti1-retention.log`.
- `uv run --frozen pytest -p no:cacheprovider tests/inventory tests/issues/v14_asset_owner tests/api/v14_data_fields tests/api/v14_data_assets`:
  47 passed; `/tmp/telescreen-v14-p07-ti1-pytest.log`.
- `uv run --frozen pytest -p no:cacheprovider`: 1095 passed, 11 skipped;
  `/tmp/telescreen-v14-p07-ti1-pytest-all.log`.
- `uv run --frozen ruff format --check --no-cache tests/inventory/retention` and
  `uv run --frozen ruff check --no-cache tests/inventory/retention`: passed;
  `/tmp/telescreen-v14-p07-ti1-ruff-format.log`, `/tmp/telescreen-v14-p07-ti1-ruff-check.log`.
- `uv run --frozen ty check tests/inventory/retention`: passed;
  `/tmp/telescreen-v14-p07-ti1-ty.log`.
- `uv run --frozen python tools/typelint.py`: 591 files clean;
  `/tmp/telescreen-v14-p07-ti1-typelint.log`.
