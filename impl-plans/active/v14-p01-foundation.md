# V14-P01 Foundation: Vocabulary, Permissions, Settings, Ports, Item Keys, Template, Police Contract 1.2

**Status**: Ready
**Design Reference**: design-docs/specs/design-v1-4-data-inventory.md sections 3, 6.3, 6.4, 8.1, 13; design-docs/specs/design-police-contract.md "Contract 1.2"
**Created**: 2026-10-13
**Last Updated**: 2026-10-13

## Plan Metadata

- planId: `v14-p01-foundation`
- planPath: `impl-plans/active/v14-p01-foundation.md`
- wave: 1
- dependsOn: none
- writePaths:
  - `impl-plans/active/v14-p01-foundation.md`
  - `src/telescreen/core/vocabulary.py`
  - `src/telescreen/core/permissions.py`
  - `src/telescreen/core/config.py`
  - `src/telescreen/ports/inventory.py`
  - `src/telescreen/ports/collectors.py`
  - `src/telescreen/ports/findings.py`
  - `src/telescreen/ports/notifications.py`
  - `src/telescreen/masking/fingerprint.py`
  - `src/telescreen/notifications/templates/builtins.py`
  - `src/telescreen/notifications/templates/variables.py`
  - `src/telescreen/notifications/templates/synthetic.py`
  - `src/telescreen/contracts/police/v1`
  - `src/telescreen_police_sdk/models.py`
  - `src/telescreen_police_sdk/masking.py`
  - `examples/compose/.env.example`
  - `README.md`
  - `src/telescreen/api/common/errors.py`
  - `src/telescreen/contracts/validator.py`
  - `pyproject.toml`
  - `uv.lock`
  - `mise.toml`
  - `.gitignore`
  - `src/web/package.json`
  - `src/web/bun.lock`
  - `tests/core/v14`
  - `tests/ports/v14`
  - `tests/masking/v14`
  - `tests/notifications/v14`
  - `tests/contract/test_schemas.py`
  - `tests/contract/test_sdk_contract.py`
  - `tests/contract/test_v14_contract.py`
  - `tests/police_sdk/test_v11_exposures.py`
  - `tests/police_sdk/test_v14_inventories.py`
  - `tests/notifications/v12/test_builtin_parity.py`
  - `tests/core/v12/test_permissions_and_oidc_settings.py`
- sharedPaths: none
- artifactRoots: none

The six root and web package files are owned by this plan per the intake rule, but they are
expected to stay byte-identical.

Read `impl-plans/conventions.md` first.

## Intent and Context

This plan pins every contract that the wave-2 and wave-3 plans build on. Afterwards they
can work in parallel without editing shared files. Concretely:

- enums, permissions and the one new setting;
- the inventory port DTOs and Protocols;
- the optional new fields on the collector, finding and notification DTOs;
- the item-key HMAC (design D10);
- the `system.data_overdue` built-in template;
- police contract 1.2 (schema plus SDK).

Every new field has a default, so no existing caller changes. This plan adds no behavior
to issues, collectors or police; p05 wires the hooks.

Imitate:

- `core/vocabulary.py:NotificationCategory` and the `StrEnum` style;
- `core/permissions.py:Permission`, `READ_PERMISSIONS` and `WRITE_PERMISSIONS`;
- `core/config.py:_weekly_report_cron` for cron validation;
- `ports/issues.py:NoopFindingsObserver`, a no-op Protocol implementation;
- `masking/fingerprint.py:Fingerprinter.resource_key`, which uses the identity secret;
- `notifications/templates/builtins.py` and `variables.py:EVENT_GROUPS` for the
  `weekly_report` and `alert_fired` entries;
- `contracts/police/v1/resource-exposure.schema.json` and its `result-envelope` reference,
  for the 1.1 pattern;
- `telescreen_police_sdk/models.py:ResourceExposure` and the
  `ResultEnvelope.resource_exposures` serialization;
- `telescreen_police_sdk/masking.py:external_fingerprint`.

## Non-goals

- No DB models (p02), no inventory services (p04, p07) and no hook calls (p05).
- No dependency change. `pyproject.toml`, `uv.lock`, `mise.toml`, `.gitignore` and the web
  package files stay byte-identical.
- No new notification category.

## Invariants

- Existing constructors of `ResourceContent`, `ResourceScan`, `ExternalScan`,
  `CollectorConfig` and `NotificationContext` keep working, because every new field is
  last and has a default.
- `telescreen_police_sdk` imports only stdlib and httpx.
- Police envelopes at contract versions 1.0 and 1.1 still validate.
- Strict typing: no `Any`, `object`, `dict` or `**kwargs`; PEP 695 aliases only.

## Deliverables

1. `core/vocabulary.py` new StrEnums, with the exact values of design section 3:
   - `InventoryMode`, `DataAssetOrigin`, `DataAssetState`, `DataAssetKind`;
   - `DataEventKind`, `DataItemKind`, `DataFieldType`, `DataFieldRole`, `DataAssetTab`.

   Additions to existing enums:
   - `NotificationEvent.DATA_OVERDUE = "data_overdue"`;
   - `EngineTaskKind.DATA_OVERDUE_CHECK = "data_overdue_check"`;
   - `ErrorCode.FIELD_IN_USE = "field_in_use"`, `REQUIRED_FIELD_MISSING`,
     `INVALID_FIELD_VALUE`.

   In `api/common/errors.py:http_status_for`, add `FIELD_IN_USE` to the 409 tuple and
   `REQUIRED_FIELD_MISSING` and `INVALID_FIELD_VALUE` to the 422 tuple. Unmapped codes
   fall through to 500.
2. `core/permissions.py`:
   - `DATA_READ = "data.read"`, which joins `READ_PERMISSIONS` automatically;
   - `DATA_WRITE = "data.write"`, added to the explicit `WRITE_PERMISSIONS` tuple;
   - `DATA_FIELDS_ADMIN = "data_fields.admin"`, admin only.
3. `core/config.py`: `TelescreenSettings.data_overdue_cron: str = "0 9 * * *"`, read from
   `TELESCREEN_DATA_OVERDUE_CRON`. An empty value disables the check. An invalid value
   raises `ConfigError("TELESCREEN_DATA_OVERDUE_CRON is invalid")`. Copy the
   weekly-report helper.
4. New `ports/inventory.py`. Signatures are binding:
   ```python
   ItemObservation(fingerprints: tuple[str, ...] | None, count: int | None)
   ValueFingerprint(fingerprint: str, key_version: int)
   ResourceObservation(source: str, resource_key: str, display_locator: str, display_title: str,
       collector_config_id: str | None, scan_job_id: str | None, observed_at: datetime,
       complete: bool, removed: bool, sweep_covered: bool,
       values: tuple[ValueFingerprint, ...], items: ItemObservation | None,
       kind_hint: DataAssetKind | None)
   class InventoryObserver(Protocol):
       async def observe(self, session: AsyncSession, observation: ResourceObservation) -> None: ...
   class InventoryReconciler(Protocol):
       async def reconcile_sweep(self, session: AsyncSession, collector_config_id: str, seen_before: datetime) -> int: ...
       async def reconcile_listing(self, session: AsyncSession, collector_config_id: str, listed_keys: frozenset[str]) -> int: ...
       async def stop_tracking(self, session: AsyncSession, collector_config_id: str, actor_ref: str) -> int: ...
   class AssetOwnerResolver(Protocol):
       async def fallback_owner(self, session: AsyncSession, source: str, resource_key: str, display_locator: str) -> str | None: ...
   ```
   Also add `NoopInventoryObserver` and `NoopInventoryReconciler`; the reconciler methods
   return 0. `ItemObservation.__post_init__` raises `TelescreenError(VALIDATION_ERROR,
   ...)` unless exactly one member is set. A static detail string is enough.
5. `ports/collectors.py`:
   - `ItemReport(keys: tuple[str, ...] | None = field(default=None, repr=False), count:
     int | None = None)`;
   - `ResourceContent.items: ItemReport | None = None` and `asset_kind: DataAssetKind |
     None = None`, appended after `removed`;
   - `CollectorConfig.inventory_mode: InventoryMode = InventoryMode.SENSITIVE_ONLY`,
     appended last.
6. `ports/findings.py`:
   - `ResourceScan.items: ItemObservation | None = None` and `asset_kind: DataAssetKind |
     None = None`;
   - new `LocatorInventory(locator: str = field(repr=False), item_fingerprints: tuple[str,
     ...] | None = None, item_count: int | None = None, kind: DataAssetKind | None =
     None)`. Mind the field order: `locator` comes first and has no default, so declare it
     `field(repr=False)` with no default;
   - `ExternalScan.inventories: tuple[LocatorInventory, ...] = ()`.
7. `ports/notifications.py`: `NotificationContext` gains `data_short_id`, `data_name`,
   `data_locator`, `data_deletion_due` and `data_link`, all `str | None = None`.
8. `masking/fingerprint.py`: `Fingerprinter.item_key(self, resource_key: str,
   raw_item_key: str) -> str`.
   - Compute `item_secret = HMAC-SHA256(identity_secret, b"telescreen:data-item:v1")`
     once per call, or cache it in a private field. Then return
     `hex(HMAC-SHA256(item_secret, (resource_key + "\0" + raw_item_key).encode()))`.
   - Always use `identity_secret` (key version 1), never the active key.
   - Never log the raw key or include it in an exception.
9. Built-in template `system.data_overdue`. Template variables are dotted paths mapped to
   `NotificationContext` attributes; imitate `variables.py:VARIABLE_FIELDS` and
   `builtins.py` `WEEKLY_REPORT`. The `NotificationContext` attribute names stay
   `data_*`.
   - `variables.py` appends these `VARIABLE_FIELDS` pairs:
     - `("data.short_id", "data_short_id")`;
     - `("data.name", "data_name")`;
     - `("data.locator", "data_locator")`;
     - `("data.deletion_due", "data_deletion_due")`;
     - `("data.link", "data_link")`.

     Also add a group constant `_DATA: str = "data"` and the `EVENT_GROUPS` entry
     `(NotificationEvent.DATA_OVERDUE, (_DATA,))`.
   - `builtins.py` adds a `_builtin(NotificationEvent.DATA_OVERDUE, ...)` entry:
     - subject `"Data past its deletion date: {{ data.short_id }}"`;
     - body `"{{ data.name }}\n{{ data.locator }}\nDeletion due: {{ data.deletion_due }}\n{{ data.link }}"`.
   - Never write `{{data_short_id}}`. Underscored names are attributes, not template
     paths.
   - `synthetic.py` gains clean synthetic constants for the five attributes: `DATA-1`,
     "Example dataset", `example://dataset/1`, `2026-01-31` and a link under the
     synthetic base URL.
10. Police contract 1.2, under `src/telescreen/contracts/police/v1/`:
    - new `resource-inventory.schema.json`, closed (`additionalProperties: false`):
      - `locator`: string, `maxLength` 1024, required;
      - `item_fingerprints`: array, `maxItems` 10000, items matching `^[0-9a-f]{64}$`;
      - `item_count`: integer, `minimum` 0, `maximum` 9007199254740991;
      - `kind`: enum of the 7 kinds;
      - `"not": {"required": ["item_fingerprints", "item_count"]}`;
    - `result-envelope.schema.json` gains `resource_inventories`: array, `maxItems` 1000,
      items `$ref` to the new schema;
    - `openapi.json` references the new schema;
    - `examples/result-envelope-1.2.json`: a valid `detect` result with one finding and
      two inventories (fingerprints, and count).

    Register `resource-inventory.schema.json` in `contracts/validator.py:_SCHEMA_NAMES`,
    next to `resource-exposure.schema.json`. `$ref` resolution uses that list.
11. SDK `models.py`:
    - frozen `ResourceInventory(locator, item_fingerprints: tuple[str, ...] = (),
      item_count: int | None = None, kind: str | None = None)`. `to_json` omits empty or
      None members; `from_json` parses back;
    - `ResultEnvelope.resource_inventories: tuple[ResourceInventory, ...] = ()`,
      serialized only when non-empty, exactly like `resource_exposures`;
    - `CONTRACT_VERSION = "1.2"`.

    SDK `masking.py`: `item_fingerprint(item_key: str, key: bytes) -> str`, the HMAC-SHA256
    hex of `item_key` with the worker key. It mirrors `external_fingerprint`; the
    docstring says the key must be stable across scans.
12. `examples/compose/.env.example`: add the commented line
    `# TELESCREEN_DATA_OVERDUE_CRON=0 9 * * *` next to the digest line.
13. `README.md`: a "v1.4 data inventory" feature section, written from the design. It
    covers the register and inventory modes, deletion and restore semantics, list items,
    the field schema, retention, the Data UI, CSV export, the ingest and police 1.2 member
    tables, the new permissions and the new env variable. Keep it short and factual, with
    no claims beyond the design.

## Pitfalls

- Do not put `DataAssetKind` in `ports/collectors.py` by redefining it. Import it from
  `core.vocabulary`. `ports/findings.py` imports `ItemObservation` from `ports/inventory`;
  avoid import cycles, because `ports/inventory` must not import `ports/findings`.
- `ResourceContent` has non-default fields `raw_locator` and `display_title` after the
  defaulted ones. Check how the dataclass orders fields today before appending; append
  only after the last defaulted field (`removed`).
- `READ_PERMISSIONS` is computed from `.read`, so do not also list `DATA_READ`
  explicitly.
- The schema `not/required` must not reject an inventory that has only `locator`.
- Do not bump the URL major version or rename existing schema files.

## Tests

New `tests/core/v14/test_foundation_v14.py`:

- each new enum has exactly the values of design section 3;
- role permission sets: a viewer has `data.read` and not `data.write`; a responder has
  both and not `data_fields.admin`; an admin has all three;
- `TELESCREEN_DATA_OVERDUE_CRON`: unset gives `0 9 * * *`; an empty value disables; `bad
  cron` raises `ConfigError`.

New `tests/ports/v14/test_inventory_ports.py`:

- `ItemObservation(None, None)` raises, and so does `(("a",), 3)`;
- the no-op observer and reconciler satisfy the Protocols and return None or 0;
- `ResourceContent`, `ResourceScan`, `ExternalScan`, `CollectorConfig` and
  `NotificationContext` build with their old positional arguments, and the new fields
  take their defaults;
- `repr(ItemReport(keys=("secret-key",)))` does not contain `secret-key`.

New `tests/masking/v14/test_item_key.py`:

- the same inputs give the same 64-hex value;
- a different `resource_key` gives a different value;
- the value differs from `resource_key` and from `fingerprint` for the same string;
- after a key rotation (`from_versions` with active version 2) the value is unchanged;
- the raw key does not appear in the output.

New `tests/notifications/v14/test_data_overdue_template.py`:

- `allowed_paths(NotificationEvent.DATA_OVERDUE)` equals exactly `("data.short_id",
  "data.name", "data.locator", "data.deletion_due", "data.link")`;
- the built-in validates against the template validator (no unknown variable);
- rendering it with the synthetic context leaves no `{{` in the subject or body. `tests/notifications/v12/test_builtin_parity.py`
keeps passing with the new event; update it only if it hard-codes the event count.

Contract tests:

- `tests/contract/test_v14_contract.py`: the 1.2 example validates. An inventory with both
  `item_fingerprints` and `item_count` fails. A bad fingerprint pattern fails. An
  inventory with only `locator` passes. 1.0 and 1.1 examples still validate.
- `tests/contract/test_schemas.py` and `tests/contract/test_sdk_contract.py`: update only
  if they enumerate example or schema files.

SDK tests:

- `tests/police_sdk/test_v11_exposures.py`: the `CONTRACT_VERSION` assertion becomes
  `"1.2"`;
- `tests/police_sdk/test_v14_inventories.py`: an SDK envelope with inventories serializes
  and validates against the schema files, and round-trips through `from_json`;
  `item_fingerprint("k", b"x" * 32)` is 64 hex and stable; an envelope with no
  inventories omits the member.

## Verification

Start every shell with `set -o pipefail; export PYTHONDONTWRITEBYTECODE=1`. `<src>` is
`src/telescreen/core src/telescreen/ports src/telescreen/masking src/telescreen/notifications/templates src/telescreen/api/common/errors.py src/telescreen/contracts src/telescreen_police_sdk`.
`<tests>` is `tests/core tests/ports tests/masking tests/notifications tests/contract tests/police_sdk`.

```bash
uv run --frozen ruff format --check --no-cache <src> <tests>
uv run --frozen ruff check --no-cache <src> <tests>
uv run --frozen ty check <src> <tests>
uv run --frozen python tools/typelint.py > /tmp/telescreen-v14-p01-typelint.log 2>&1; grep -E '^(src/telescreen/(core|ports|masking|notifications/templates|api/common/errors.py|contracts)|src/telescreen_police_sdk|tests/(core|ports|masking|notifications|contract|police_sdk))' /tmp/telescreen-v14-p01-typelint.log
uv run --frozen pytest -p no:cacheprovider <tests> 2>&1 | tee /tmp/telescreen-v14-p01-pytest.log; echo "exit=$?"
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-v14-p01-pytest-all.log 2>&1; echo "exit=$?"
git diff --quiet -- pyproject.toml uv.lock mise.toml .gitignore src/web/package.json src/web/bun.lock; echo "exit=$?"
```

Expected: every scoped command exits 0, the typelint grep prints nothing, and the
dependency diff check exits 0. The full suite is informational. A failure in a file
outside `writePaths` is a Reconciliation request, not an edit.

## Completion Criteria

- [ ] All 13 deliverables are present. The signatures in deliverable 4 match exactly.
- [ ] The scoped checks pass and the typelint grep is empty.
- [ ] The root and web package files are unchanged.
- [ ] `http_status_for(FIELD_IN_USE) == 409`. `REQUIRED_FIELD_MISSING` and
  `INVALID_FIELD_VALUE` map to 422 (asserted in `tests/core/v14`). The validator loads
  the new schema.
- [ ] The Progress Log records hashes, and every command with its exit status and log
  path.

## Progress Log

(none yet)
