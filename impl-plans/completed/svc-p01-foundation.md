# SVC-P01 Foundation: Contracts, Settings, Topology, Pure Template and Rule Libraries

**Status**: Completed
**Design Reference**: design-docs/specs/design-service-split.md sections 3, 4.1-4.4, 7, 9, 10, 14, C7, C2
**Created**: 2026-10-15
**Last Updated**: 2026-10-15

## Plan Metadata

- planId: `svc-p01-foundation`
- planPath: `impl-plans/active/svc-p01-foundation.md`
- wave: 1
- dependsOn: none
- writePaths:
  - `impl-plans/active/svc-p01-foundation.md`
  - `pyproject.toml`
  - `README.md`
  - `src/telescreen/core/config.py`
  - `src/telescreen/core/topology.py`
  - `src/telescreen/core/vocabulary.py`
  - `src/telescreen/core/templating.py`
  - `src/telescreen/core/ownership_match.py`
  - `src/telescreen/workflow/dsl/model.py`
  - `src/telescreen/workflow/dsl/templates.py`
  - `src/telescreen/issues/ownership.py`
  - `src/telescreen/notifications/templates/render.py`
  - `src/telescreen/notifications/templates/validate.py`
  - `src/telescreen/notifications/__init__.py`
  - `src/telescreen/ports/notification_types.py`
  - `src/telescreen/ports/directory_projection.py`
  - `src/telescreen/ports/notification_commands.py`
  - `src/telescreen/ports/inventory_commands.py`
  - `src/telescreen/ports/notifications.py`
  - `src/telescreen/inventory_service/transport.py`
  - `tests/ports/split`
  - `tests/core/split`
  - `tests/test_package.py`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first. This is the only plan that edits `pyproject.toml`
and `README.md`. Do not run `uv lock`: no dependency changes.

## Intent and Context

Pin every cross-plan contract so that the wave 2-5 plans can work in parallel:

- the wire codecs (directory projection, issue link state, notification commands);
- the new settings and the topology rule;
- the two new engine task kinds;
- the receipt and request fields;
- the pure template and ownership-rule libraries;
- the configurable service transport prefix;
- the `telescreen-notification` script entry.

Nothing here changes single-process behavior. Imitate:

- `src/telescreen/ports/inventory_commands.py` (frozen dataclasses, `Annotated` pydantic
  limits, `TypeAdapter`, encode validates by decoding, decode raises
  `TelescreenError(VALIDATION_ERROR, static detail)` and calls `require_payload_size`);
- `src/telescreen/ports/retention.py` (`retention_event_id` uuid5 helper);
- `src/telescreen/core/config.py:_optional`/`_integer` for settings.

## Non-goals

- No database models, migrations or services. No wiring.
- Do not remove `bureau_service_url` from settings (svc-p04 stops reading it; the field
  stays parsed and unused).
- Do not edit `src/telescreen/notifications/sender.py` or `dlp.py` (svc-p05).

## Deliverables and File-Level Changes

1. `ports/directory_projection.py` (new). Contents:
   - `DirectoryEntityKind(StrEnum)`: `member`, `team`, `identity`, `api_token`,
     `ownership_rules`, `collector`.
   - Payload dataclasses:
     - `MemberEntity(id, handle, display_name, active: bool, role: str | None, email: str | None, slack_user_id: str | None)`
     - `TeamEntity(id, slug, name, lead_member_id: str | None, member_ids: tuple[str, ...], email: str | None, slack_channel: str | None)`
     - `IdentityEntity(id, member_id, kind: str, identity_key: str)`
     - `ApiTokenEntity(id, label: str, token_hash: str, scopes: tuple[str, ...], member_id: str | None, revoked: bool)`
     - `OwnershipRuleEntry(id, position: int, source: str | None, pattern: str, team_id: str, enabled: bool)`
     - `OwnershipRulesEntity(rules: tuple[OwnershipRuleEntry, ...])`
     - `CollectorEntity(id, name)`
   - `type DirectoryPayload = MemberEntity | TeamEntity | ...` with a `kind` Literal
     discriminator on each payload.
   - `DirectoryEntity(kind, id <= 36 chars, revision >= 1, present: bool, payload: DirectoryPayload | None)`.
     The codec enforces two rules: `present` must equal `payload is not None`, and
     `payload.kind` must equal `kind`. The id of `ownership_rules` must be `"rules"`.
   - `encode_directory_entity(entity) -> str` and `decode_directory_entity(str) -> DirectoryEntity`.
   - `for_inventory(entity) -> DirectoryEntity`: removes email/slack fields (sets them to
     None) from members and teams.
   - **Design correction**: `ApiTokenEntity.label` is required, because
     `ports/directory.py:ActorDirectory` displays `token:<id>` as the token label (an
     unchanged `/api/data` response). svc-p08 records this in the design doc.
2. `ports/inventory_commands.py`: add `InventoryCommandKind.ISSUE_LINK = "issue_link"` and
   `DIRECTORY = "directory"`, plus two operation types:
   - `IssueLinkState(issue_id, resource_key, short_id, status: str, owner_ref: str | None, present: bool, kind=ISSUE_LINK)`
   - `DirectoryUpdate(entity: DirectoryEntity, kind=DIRECTORY)`

   Add both to the `InventoryOperation` union. Keep `schema_version` at 1 (additive kinds,
   design 4.2). Collection-size checks are unchanged.
3. `ports/notification_types.py` and `ports/notification_commands.py` (new).
   - Define `ContextFieldHits(field: str, count >= 1)` and
     `ProducerRedactionHits(subject >= 0, body >= 0, context: tuple[ContextFieldHits, ...] = ())`
     in `ports/notification_types.py`, beside `NotificationContext`.
   - Re-export them from `ports/notification_commands.py`. This avoids an import cycle with
     `ports/notifications.py`, which must not import `notification_commands`.
   - Add `STRING_CONTEXT_FIELDS: tuple[str, ...]` to `notification_types.py`, built with
     `dataclasses.fields` from the `str | None` fields of `NotificationContext`. Exclude
     `issue_risk_score` and `task_tier`, which are ints.
   - Validation lives in the `notification_commands` codec. It rejects a `field` not in
     `STRING_CONTEXT_FIELDS`, duplicates, and more entries than `len(STRING_CONTEXT_FIELDS)`.

   `ports/notification_commands.py` contents:
   - `SendOperation`: every `NotificationRequest` field except `id_seed` and `prior_hits`,
     plus `redaction: ProducerRedactionHits`, with `kind="send"` and `recipients` 1..64.
   - `MemberSendOperation`: the `MemberNotification` fields plus `redaction`, with
     `kind="member_send"`. The codec requires `redaction.subject == redaction.body == 0`.
   - `DirectoryOperation(entity, kind="directory")`.
   - `NotificationCommand(event_id 1..36, occurred_at, operations: 1..8, schema_version 1)`.
   - `NotificationCommandReceipt(StrEnum)`: `applied`, `duplicate`, `deferred`.
   - Codecs: `encode_notification_command` and `decode_notification_command`.
   - `operation_seed(event_id, op_index) -> str` returns `f"{event_id}:{op_index}"`.
   - `notification_row_id(id_seed, recipient_index) -> str` returns
     `str(uuid5(NAMESPACE_URL, f"telescreen:notification:{id_seed}:{recipient_index}"))`.
   - `ChannelStatus(email: bool, slack: bool)` and `TestRequest(channel, member_id)` for
     the 4.4 reads.
4. `ports/notifications.py`:
   - `NotificationRequest` gains `id_seed: str | None = None`,
     `prior_hits: ProducerRedactionHits | None = None` (imported from
     `ports.notification_types`) and `skip_unknown: bool = False`.
   - `MemberNotification` gains `id_seed` and `prior_hits` with the same defaults.
   - `NotificationReceipt` gains `pending: bool = False`.
   - Update the docstrings to state the defaults keep single-process behavior.
5. `core/vocabulary.py`: add exactly `EngineTaskKind.NOTIFICATION_COMMAND_DELIVERY =
   "notification_command_delivery"` and `PROJECTION_RELAY = "projection_relay"`. The file
   is 975 lines and must stay under 1000.
6. `core/config.py` adds these `TelescreenSettings` fields and env names:

   | Field | Env name | Default |
   | --- | --- | --- |
   | `notification_service_url` | `NOTIFICATION_SERVICE_URL` | None |
   | `notification_service_token_ref` | `NOTIFICATION_SERVICE_TOKEN` | None |
   | `notification_inventory_token_ref` | `NOTIFICATION_INVENTORY_TOKEN` | None |
   | `notification_database_url` | `NOTIFICATION_DATABASE_URL` | None |
   | `notification_replica_grace_seconds` | `NOTIFICATION_REPLICA_GRACE_SECONDS` | 600, minimum 1 (`_integer`) |

7. `core/topology.py` (new). Contents:
   - `Topology(StrEnum)`: `single_process`, `split`.
   - `topology_of(settings) -> Topology` raises `ConfigError` in four cases:
     - exactly one of `inventory_service_url`/`notification_service_url` is set
       (detail "inventory and notification service URLs must be configured together");
     - single process with `notification_database_url` set and different from
       `database_url`;
     - split without `inventory_service_token_ref`;
     - split without `notification_service_token_ref`.

   Keep it pure: no I/O.
8. `core/templating.py` (new): move `TemplatePart`, `type Template` and `parse_template`
   unchanged from `workflow/dsl/model.py:64-71` and `workflow/dsl/templates.py`. In
   `workflow/dsl/model.py` and `workflow/dsl/templates.py`, re-import them so existing
   imports keep working (`from telescreen.core.templating import TemplatePart as
   TemplatePart`). `render` and `RunContext` stay in `workflow/dsl/templates.py`.
9. `core/ownership_match.py` (new):
   - Protocol `RuleLike` with read-only attributes `id`, `position`, `source`,
     `pattern`, `team_id`, `enabled`;
   - `match_rule[R: RuleLike](rules: Sequence[R], source: str, locator: str) -> R | None`,
     moved from `issues/ownership.py:53`.

   `issues/ownership.py` re-exports it. Its behavior is identical (sorted by
   (position, id), enabled only).
10. `notifications/templates/render.py` and `validate.py`: import `Template` and
    `parse_template` from `telescreen.core.templating`, with no `telescreen.workflow`
    import. Add two pure helpers to `render.py` and make `render_for_channel` use the first:
    - `channel_texts(texts: TemplateTexts, channel) -> tuple[str, str]`, factored out of
      `render_for_channel` (the same selection logic);
    - `field_occurrences(texts, channel) -> tuple[tuple[str, int], ...]`: the number of
      times each `NotificationContext` field name appears as a variable in the selected
      subject and body, mapped through `variables.VARIABLE_FIELDS`. svc-p05 uses it for
      prior-hit weighting.
11. `inventory_service/transport.py`: `ServiceHttp` gains `prefix: str = PREFIX` and uses
    it instead of the module constant. Add `NOTIFICATION_PREFIX =
    "/internal/notification/v1"`. Existing behavior is unchanged.
12. `pyproject.toml`: under `[project.scripts]` add
    `"telescreen-notification" = "telescreen.notification_service.cli:main"`. The wheel
    packages are already correct.
14. **`notifications/__init__.py` lazy (review F2).** Remove the eager re-exports, keeping
    only the module docstring and `__all__: list[str] = []`.
    - Today importing `telescreen.notifications.dlp` loads `sender`, `delivery`, `email`,
      `slack` and `repository` through the package init. That breaks the svc-p06 split
      import gate, which allows only `notifications.dlp`/`notifications.guard`.
    - Before editing, run `grep -rln "from telescreen.notifications import" src tests` and
      log the output (expected: empty). If anything matches, switch those imports to the
      submodule path and add the file to a Reconciliation request instead of editing it.
    - The change is behavior-neutral.
13. `README.md`: add a "Split deployment" section (about 25 lines) taken from design sections
    3, 13 and 14. Include the topologies, the three commands, the settings table, the
    compose file `examples/compose/docker-compose.services.yml` and a pointer to the
    design. Placeholders only, no secrets. Replace every README reference to
    `docker-compose.inventory.yml` with `docker-compose.services.yml`. svc-p08 creates the
    new file and deletes the old one.

## Key Pitfalls

- Codecs must use `strict=True` and must never echo content in errors.
- Do not import `telescreen.db` in any `ports` module.
- `DirectoryEntity` uses discriminated payloads. Do not use `dict` or `Any`; the payload
  union needs a `Field(discriminator="kind")`.
- `ProducerRedactionHits` is count-only. Never add spans, rule ids or text.
- `workflow.dsl` re-exports must keep `from telescreen.workflow.dsl.model import Template`
  working (about 40 importers); run the workflow tests.
- Moving `match_rule` must keep `issues/routing.py` and `issues/asset_owner.py` working
  unchanged.

## Tests (`tests/ports/split`, `tests/core/split`, `tests/test_package.py`)

Codecs:

- A round-trip of each new codec gives an equal object.
- Unknown `schema_version` 2 -> `TelescreenError` `validation_error`, and the detail does
  not contain the payload.
- `present=True` with payload None -> rejected. A kind/payload mismatch -> rejected.
- `ProducerRedactionHits` with field `issue_risk_score`, an unknown field or a duplicate
  -> rejected. `MemberSendOperation` with `subject=1` -> rejected.
- 65 recipients or 9 operations -> rejected. A payload over the handoff limit -> rejected.
- `notification_row_id(operation_seed("e", 0), 1)` is stable and differs per index.
- `for_inventory` clears member and team email/slack fields.

Settings and topology:

- Only the inventory URL is set -> `ConfigError`. Both URLs without tokens -> `ConfigError`.
  Neither URL -> `single_process`. Both URLs with tokens -> `split`.
- Single process with a notification database URL different from main -> `ConfigError`.

Templates and package:

- `field_occurrences` for a template using `{{ issue.short_id }}` twice in the email body
  gives `("issue_short_id", 2)`.
- `channel_texts` equals the texts `render_for_channel` uses for Slack and email.
- `tests/test_package.py`: the `telescreen-notification` script entry is declared. Read
  `pyproject.toml` with `tomllib`; do not import the not-yet-existing module.
- `tests/core/split/test_lazy_notifications.py`: a subprocess runs
  `import telescreen.notifications.dlp, sys` and asserts that none of
  `telescreen.notifications.sender`, `.email`, `.slack` or `.delivery` is in `sys.modules`.
- `ports/notifications.py` imports without importing `ports.notification_commands`
  (subprocess `sys.modules` check).

## Verification (record exit and log; conventions section 7 preamble first)

```bash
uv run --frozen ruff format --check --no-cache src/telescreen/core src/telescreen/ports src/telescreen/workflow/dsl src/telescreen/issues/ownership.py src/telescreen/notifications/templates src/telescreen/notifications/__init__.py src/telescreen/inventory_service/transport.py tests/ports/split tests/core/split tests/test_package.py
uv run --frozen ruff check --no-cache <same paths>
uv run --frozen ty check <same paths>
uv run --frozen pytest -p no:cacheprovider tests/ports tests/core tests/workflow tests/notifications tests/issues tests/inventory_service tests/test_package.py 2>&1 | tee /tmp/telescreen-svc-p01-foundation-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-svc-p01-foundation-typelint.log 2>&1; grep -E '^(src/telescreen/(core|ports|workflow/dsl|notifications/templates)|tests/(ports|core)/split)' /tmp/telescreen-svc-p01-foundation-typelint.log
uv run --frozen pytest -p no:cacheprovider > /tmp/telescreen-svc-p01-foundation-pytest-all.log 2>&1; echo "exit=$?"
```

Evidence: every scoped command exits 0 and the typelint grep prints nothing. The full
suite exits 0: this plan changes no behavior.

## Completion Criteria

- [x] Items 1-14 delivered. `grep -c "^from" src/telescreen/notifications/__init__.py` prints 0. `wc -l src/telescreen/core/vocabulary.py` < 1000.
- [x] `grep -rn "telescreen.workflow" src/telescreen/notifications/templates` prints nothing.
- [x] All verification commands exit 0. The full suite passes.
- [x] Progress Log has hashes for every edited source and test file; the plan's own digest is in the plan-local evidence manifest to avoid self-reference.

## Progress Log

### Session: 2026-10-15 18:20 JST
**Tasks Completed**: Delivered foundation items 1-14: strict versioned directory and notification contracts, additive Inventory command kinds, notification DTO defaults, settings and topology selection, task kinds, pure template and ownership matching modules with compatibility re-exports, template helpers, configurable service prefix, CLI metadata, lazy notification imports, split deployment README section, and focused regression tests.
**Tasks In Progress**: None within svc-p01-foundation. Independent review, cross-plan integration, plan archival, commit and push remain downstream workflow steps.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**:
- `README.md` before=732ddeb26ed1c5cf81a4d90228a934418748e70d13cd839bffd0b3334a3be506 after=8f7a3be5a91ae163912a6ac41b9ba5de45535e6c30769b1103065d226d8a221c
- `pyproject.toml` before=3f930ffb6be836624861d0c8955a4a7fef45650362437baa702410ea0307d360 after=e197cd7c352bab509b71438c891c2a240be4e3afac2485cd931d61f48c0ebdc5
- `src/telescreen/core/config.py` before=1e3b23d8c577ffa5d7da885c233eee7cede3e5da74a5fbe3f2e3b8c36e484c73 after=385c62806bd8564577e0458a7f7f1bceedd540ffcf95ca2915dc9f300b3b6ccf
- `src/telescreen/core/vocabulary.py` before=4fb2d7292f1262fada3b8aa329054fdeca158cc10d5eb14d4b118c4331d282d2 after=d54d0ad323167783ff61f78db4774e1190f2a878ffd41946eac6d0f5778c134d
- `src/telescreen/core/topology.py` before=absent after=86fe59167768d38ce6e92e70522d8b3d139dfb1563064503189711b2a59fdc0a
- `src/telescreen/core/templating.py` before=absent after=e57f6629cf318e328cc5829f7886246d1c257e84730fc75dfb1a590abcfb8248
- `src/telescreen/core/ownership_match.py` before=absent after=2f671236336ef1427410d174e98b553ebc2497933167cee862feeafe6bb7d6ca
- `src/telescreen/workflow/dsl/model.py` before=8016b67903b1461e69f2339ce48a97c8a71f570f4648b1f9ad66c6566330e507 after=26092d9e51b303e55bd45965d25176ae2b65ad432a369f08f9060d5d747f956c
- `src/telescreen/workflow/dsl/templates.py` before=8e78131debf8b0caf9267ff8420a56fe95ddd69c4ada278c6a20aa117166137a after=99dcec624fd14e6ccdf207c90e7ec0838301389022340380db94f07f2e9295e2
- `src/telescreen/issues/ownership.py` before=c93c9759c3f4c55602289579e4510159520dafe3ffd26c76768f8dc32758e0e7 after=281856f0d772785b308068bc74b8ebe957f828a42a09747daf3e07ae1daa4a4b
- `src/telescreen/notifications/templates/render.py` before=68638999238e0267b805d8bcab8148608691ca2bbddcc1be2994d2202c07abd9 after=7615dd58220aed5e3261afcb0121eb78674c54453d3d98cbfa5ffa8a1fe06025
- `src/telescreen/notifications/templates/validate.py` before=e8ab6359969eba9fd704a7d3e4f60d723006e8298fa0fa9ed0c25864cd674b37 after=877b5ac8695dd0894d0a82cb2e342c8150441a604862f889d9d92492b3b23055
- `src/telescreen/notifications/__init__.py` before=2fd18f24b1ec295fa6320ea169aeccd6a9719a90fc2fc6a4e7c393352e487013 after=018d098f847af8af1995d9c4c8c3b027636bcb3eb495c85faf096704efbb898e
- `src/telescreen/ports/notification_types.py` before=cc63a4e242f0472b11c8738c6dff6a5be5f9b3d3374209353c3e4fe2ba1a1493 after=03291dab047ffe70daca812c028433065fb276ab0e0ee2e97fbee1277973bed4
- `src/telescreen/ports/directory_projection.py` before=absent after=38e13272d3f233880bc0609dc48131b0a7ad20b7017002f0c65043a298b169d4
- `src/telescreen/ports/notification_commands.py` before=absent after=36b8f85716cd4e19c64fb42c0111c5d7fde8eb2cbf039828d703f006d47f8cbd
- `src/telescreen/ports/inventory_commands.py` before=93cafa5f385cee95823427cc252d0196328987cff7fe76f094c59977805f2db7 after=030ada5075f18fa8ae5552a34c9e958698f8682473924e6398c3e927d97fa14a
- `src/telescreen/ports/notifications.py` before=96f86280ada3f960b10aceb78d041743f8be1bba98188197e55d34172bac8b97 after=0954a7b2724c2a6e8b48e3ea64a5eb6dbf006a00c3b6f2d5f0e32a771b83cb57
- `src/telescreen/inventory_service/transport.py` before=26a1ffa6e1270e9d5ab8fb4573f2bfc067c027ccafe918320aef718e786ca7c5 after=5015be012b03b3f9e67f90c1372b9ca5add98e5ec538efb8c7343bf31df1e56d
- `tests/ports/split/test_directory_projection.py` before=absent after=70054a15cae5ddee6d6119b52e7578798f8dd18ec5c4c36a1092a2a0e100623c
- `tests/ports/split/test_inventory_command_extensions.py` before=absent after=41d69f31adc3417c46939d7e4f771241d641a303bb7da81c76fed9642cec2692
- `tests/ports/split/test_notification_commands.py` before=absent after=7d8a2bd7fd661aae724b2e3e62db24e8a98f1d0128b834e1da57d92f99f14955
- `tests/core/split/test_lazy_notifications.py` before=absent after=3dc053049f97acd39656791e033f7508b2ff9c84cacebc16d096a7c29659c760
- `tests/core/split/test_templating.py` before=absent after=dd6efe91bcb4d6b66234c557d214308b84a147783a50d482c2b13d0de5b4f621
- `tests/core/split/test_topology.py` before=absent after=d4424dc1865966ba8d28e9189a3bcff76d20e13587d06938360649800f33d840
- `tests/core/split/test_transport_prefix.py` before=absent after=0d7c316be2c4ceca69b631da29a7fa38f74283c39142be9004b4ba2b90153c63
- `tests/test_package.py` before=8a01540627d6bf93e0137bc458f56e5d67f5e106d380e3e942b950a23a949ab2 after=72f06a1d0754504566727cc43c94314cf40982b1329797ecf5b8c0bb78b3c4de
**Verification**:
- `uv run --frozen ruff format --check --no-cache src/telescreen/core src/telescreen/ports src/telescreen/workflow/dsl src/telescreen/issues/ownership.py src/telescreen/notifications/templates src/telescreen/notifications/__init__.py src/telescreen/inventory_service/transport.py tests/ports/split tests/core/split tests/test_package.py` -> exit 0 (log: `/tmp/telescreen-svc-p01-foundation-format.log`).
- `uv run --frozen ruff check --no-cache src/telescreen/core src/telescreen/ports src/telescreen/workflow/dsl src/telescreen/issues/ownership.py src/telescreen/notifications/templates src/telescreen/notifications/__init__.py src/telescreen/inventory_service/transport.py tests/ports/split tests/core/split tests/test_package.py` -> exit 0 (log: `/tmp/telescreen-svc-p01-foundation-ruff.log`).
- `uv run --frozen ty check src/telescreen/core src/telescreen/ports src/telescreen/workflow/dsl src/telescreen/issues/ownership.py src/telescreen/notifications/templates src/telescreen/notifications/__init__.py src/telescreen/inventory_service/transport.py tests/ports/split tests/core/split tests/test_package.py` -> exit 0 (log: `/tmp/telescreen-svc-p01-foundation-ty.log`).
- `uv run --frozen python tools/typelint.py` with owned-path grep filter -> exit 0; `typelint: 722 file(s) clean` (log: `/tmp/telescreen-svc-p01-foundation-typelint.log`).
- `uv run --frozen pytest -p no:cacheprovider tests/ports tests/core tests/workflow tests/notifications tests/issues tests/inventory_service tests/test_package.py` -> exit 0; 632 passed, 4 skipped (log: `/tmp/telescreen-svc-p01-foundation-pytest.log`).
- `uv run --frozen pytest -p no:cacheprovider` -> exit 0; 1,304 passed, 36 skipped (log: `/tmp/telescreen-svc-p01-foundation-pytest-all.log`).
- Source acceptance checks for lazy `notifications/__init__.py`, vocabulary line count, no workflow imports in templates, and no eager notification package imports -> exit 0 (log: `/tmp/telescreen-svc-p01-foundation-acceptance.log`).
- `git diff --check` -> exit 0 (log: `/tmp/telescreen-svc-p01-foundation-diff-check.log`).
**Notes**: An earlier scoped run hit the unchanged map-reduce throughput assertion at 293 seconds against its 240-second limit (log: `/tmp/telescreen-svc-p01-foundation-pytest.log` was replaced by the final passing rerun; the earlier full output was observed in the terminal). The final scoped rerun and final full suite both pass on the current tree. Initial lint diagnostics were import ordering and class-shape issues; corrected and covered by the final passing checks. Four offline integration/live skips are expected under repository conventions.
