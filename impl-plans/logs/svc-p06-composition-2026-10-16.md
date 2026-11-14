# svc-p06-composition Progress Log archive (2026-10-16, run 6)

These entries were moved unchanged from `impl-plans/active/svc-p06-composition.md` in the
run 7 planning commit (manifest g7), to keep the plan under 400 lines.

### Session 1 — implementation and scoped verification (2026-10-16)

- Implemented local and split notification composition, lazy local notification imports,
  notification proxy routes, inventory command client relocation, integration channel
  status delegation, weekly report move, and notification CLI commands. Added scoped
  regressions for split import/secret boundaries, proxy/client behavior, channel status,
  and CLI store/resync/test-send behavior.
- Current-source gates: Ruff format exit 0 (`/tmp/telescreen-svc-p06-composition-format-final.log`);
  Ruff check exit 0 (`/tmp/telescreen-svc-p06-composition-ruff-final.log`); ty exit 0
  (`/tmp/telescreen-svc-p06-composition-ty-final.log`); typelint target filter exit 0
  (`/tmp/telescreen-svc-p06-composition-typelint.log`); scoped pytest exit 0, 216 passed,
  0 failed (`/tmp/telescreen-svc-p06-composition-pytest-final.log`). The scoped run
  includes the unedited `tests/inventory_service/test_config.py`.
- Static gates exit 0: notification implementation import allowlist; weekly source move;
  no `inventory_service.clients` references; `wiring.py` below 1000 lines.
- Informational wave-B full-suite command `uv run --frozen pytest -p no:cacheprovider`
  exited 2 during collection at `tests/inventory_service/test_postgres.py`: the in-flight
  svc-p04 `tests/inventory_service/conftest.py` no longer exports `RemoteWorld`. This is
  an explicitly informational `tests/inventory_service/**` failure for this plan; the
  scoped config test passed. Full-suite log: `/tmp/telescreen-svc-p06-composition-pytest-all.log`.
- Earlier attempts were resolved on final source: focused pytest collection failed on the
  stale weekly test import (`/tmp/telescreen-svc-p06-initial-pytest.log`); the first focused
  behavioral run had 3 failures and 16 passes (`/tmp/telescreen-svc-p06-focused-pytest.log`)
  due to split Slack-ref resolution, a missing test import and router ordering; that suite
  then passed 19/19 (`/tmp/telescreen-svc-p06-focused-rerun.log`). Initial Ruff and ty
  findings were fixed and their final scoped gates pass as recorded above.
- Source SHA-256 values below compare the immutable fanout snapshot (`before`) with the
  final implementation source (`after`). `absent` denotes a new/deleted file.

| Path | Before | After |
| --- | --- | --- |
| `src/telescreen/cli.py` | `4edd5ca24324971c422a9b37d615037b3523f7dbb3f06e6e70d1d48eb8b7c358` | `69aad5c5ff090b20b73d5ebd23e7c23adae8db77197c67efd2c50b2aa1345500` |
| `src/telescreen/cli_v12.py` | `d143ea0cf2064303a364bfb1ced14a775b6736e7af9c3c635650e5a13314145d` | `64d7ba90e08301aabfa64bec65934136e9a07b69533afa40e4fbcb07804b5e5f` |
| `src/telescreen/integrations/status.py` | `b6b7d08940c1a623bc06cc63f49c302675c14e0b5bf86dbec6f54eace046f0b0` | `9e82f255f7c631f479b7b0e94ea2ab89dfa769463384abaee6b8d0d7aa5de276` |
| `src/telescreen/integrations/tests.py` | `bbb21bca5ce9bff3106875ee9f2145c6cc5a1f174ada95406d9b1cd2d9625823` | `34847e4bd3cabbea8f799547b2d53410b6da95dbe7f9a56a4ba227a7e4514bb4` |
| `src/telescreen/integrations/inventory_client.py` | absent | `ddf5d9e5ed08093500dddeb3b1e5d6982fec5a6eea23fe117efc705b98fb8910` |
| `src/telescreen/inventory_service/bureau.py` | `f302326ab995df4ebe3dc58ec00b5334cbfc5b4e3ae220980c3172fdbae063cb` | `da31cf127902f4e27e51f7c737d0519309617620deedb647b2d6e6d365480463` |
| `src/telescreen/inventory_service/clients.py` | `6f1ea6dc1b490ab543224a7e63cad195b5b2ad0cc3e0788af859cf3e6125d4a3` | absent |
| `src/telescreen/inventory_service/wiring.py` | `028d04419ea5546f61af19f9cc4e3b204e052743f6294773dc6646fc21cc4e86` | `e54db00a1f9dd94f88dc035d7c979b5f420e3620f0aeefc7e5f5f6850743c44c` |
| `src/telescreen/notifications/weekly.py` | `f2c5e49b7c7e85d014863f87492dfbc0b03a58237b038f06e502334abacfc678` | absent |
| `src/telescreen/issues/weekly_report.py` | absent | `f2c5e49b7c7e85d014863f87492dfbc0b03a58237b038f06e502334abacfc678` |
| `src/telescreen/wiring.py` | `63b9ea1264b221fbcec9492bb3ab64476996a7cebcd30dfb6215e7156d0642cd` | `77d70f622f0cac540b61ed560e5522c6bb4e89448a467f71582c8922c7eb2636` |
| `src/telescreen/wiring_inventory_parts.py` | `92fa024f78a5889288d46905aaa704443065b6b592b2a1f090c803d3361cd05a` | `862d9bc0c9b0b72c0dd27c1fd5164ff656cbe8b6123046807159b1c3d0420020` |
| `src/telescreen/wiring_notify_local.py` | absent | `73956eb4f68ca445d3bfdad4a6f1995d00b90c5bb57200e2486788e3049fa75a` |
| `src/telescreen/wiring_split.py` | absent | `a1c2946cac1bda3f5886d55d0552adedd7abfdfb9bd62c719b9cda3b637cdd89` |
| `src/telescreen/wiring_v12.py` | `a817c45e7f5c94a1d3786e1f9879a46bbc0b03fb71dc159161de30c4425499bf` | `7cce68d4d6f8ccace710ba4cccf3ba4bfaf251e28d7e53f19a9b072134f83f65` |
| `src/telescreen/wiring_v13.py` | `539a47bbe53bde20cd5509635874b50fef9bdd5acc49dc8f009e5fcbb3cd3297` | `f7066692e651fb68628df77ab84a3a51eb8ef062333af1d983bfbd8aa9af3f70` |
| `tests/e2e/test_inventory_client.py` | absent | `6fbee24551029d3ba32bb89a4c411caf317836be82c50a5c845eae7ecc544143` |
| `tests/e2e/test_wiring_split.py` | absent | `3730ff1e3ad6a9e947fc797e4591e934be1744551a73339f29b6489341dd87f0` |
| `tests/e2e/test_wiring_cli.py` | `b83c9f6800236f64b5b0af6e00cf718e3bc19207c0d445ad51399a787c2d3a95` | `545ae06c2438726fd813872876508c83b0b69e1192e1178e7e2649722af15b3f` |
| `tests/e2e/test_wiring_v13.py` | `adc054251e432b9c04d161edfd2194333a86b5e6790ceb6f8835d001a0004caf` | `c10ee7d5511eb3de098191462ff06cb7d83e8b6e6d3f4c5f2ae3b127ad1b52c9` |
| `tests/integrations/test_integrations.py` | `be05167ef605dd0c8bc718d06c374777ef72533489a5cafd60edff485dcc89c5` | `8399b9e2b8e974d25dc5602e31490d1c7862e6ce5abaae672aae157ed431f759` |
| `tests/issues/test_weekly_report.py` | absent | `6ef4bbd62ddd1e0b9ab4dcd4e8cc946a63a6d82047475721efe6c6a7c9311a84` |
| `tests/notifications/v13/test_weekly.py` | `bb390b60741c9c13268e8bce0efd9ad4f041407419b8b4086685fc1298784bd2` | absent |

### Session 2 — test-integrity repairs (2026-10-16)

- Addressed `p06-tic-F1`: declared `selected_url: str | None` in `src/telescreen/cli.py`;
  replaced the typelint grep with the absolute-or-relative path pattern shown above. The
  grep matched the absolute sample recorded in
  `tmp/telescreen-svc-20261016-g6/svc-p06-composition/typelint-grep-sanity.log`; the final
  typelint run reports `782 file(s) clean` and has no p06-path match
  (`/tmp/telescreen-svc-p06-composition-typelint-final.log`).
- Addressed `p06-tic-F2`: `tests/e2e/test_wiring_split.py` now checks all four proxy paths
  are Bureau proxy endpoints, the command-delivery/no-op/relay handlers are registered,
  an outbox row and idempotent engine task commit together with redacted content, a member
  display-name update writes projection markers, and both invalid topologies fail before
  primitives, env reads or HTTP use. `tests/integrations/test_integrations.py` now runs
  the actual `RemoteNotificationTester` email path through `MockTransport`.
- Addressed `p06-tic-F3`: `tests/e2e/test_wiring_cli.py` asserts split default migration
  creates exactly `main_database_tables()` plus `alembic_version_main`. The four legacy
  notification tables remain in that accepted main schema.
- Addressed `p06-tic-F4`: full suite with collection continuation exited 1, with 1377
  passed, 3 failed and 1 collection error. Every failure is attributed to in-flight p04:
  `tests/inventory/core/test_import_boundaries.py::test_other_business_modules_cannot_import_inventory_sql_models`
  (p04 `inventory_service/dispatch.py` imports inventory models);
  `tests/inventory_service/test_process.py::test_two_processes_use_only_their_owned_database`;
  `tests/inventory_service/test_split_inventory.py::test_inventory_cli_and_app_import_without_telescreen_composition`;
  collection error `tests/inventory_service/test_postgres.py` cannot import `RemoteWorld`
  from p04's changing `tests/inventory_service/conftest.py`. Full log:
  `/tmp/telescreen-svc-p06-composition-pytest-all-final.log`. `tests/tools/test_typelint.py`
  passed; no p06-owned failure remains.
- Regression sensitivity review: removing the notification proxy router fails the four
  path assertions; removing any required handler fails its instance assertion; removing
  the producer binding/outbox enqueue fails row/task assertions; removing the marker
  listener fails the update marker assertion; allowing resource construction before
  topology checks fails the patched primitive spy; bypassing RemoteNotificationTester
  fails the expected `/channels` and `/test` requests; changing default split migration
  ownership fails the exact table-set assertion.
- Resolved attempts retained: the first CLI rerun had 5 passes and 1 failure because the
  assertion omitted four intentionally retained tables (`/tmp/telescreen-svc-p06-wiring-cli-rerun.log`);
  the correction to `main_database_tables()` passes in the final CLI run. The first
  collection-continuing full-suite attempt completed with the same 3 p04 failures and 1
  p04 collection error, but its zsh wrapper used the reserved variable `status`; a second
  run with `pytest_exit_code` captured the actual exit and complete results
  (`/tmp/telescreen-svc-p06-composition-pytest-all-rerun.log`, then final log above).
- Final reruns: focused split and integration tests 13 passed
  (`/tmp/telescreen-svc-p06-split-tests-rerun.log`); CLI tests 6 passed
  (`/tmp/telescreen-svc-p06-wiring-cli-rerun-final.log`); complete assigned scoped suite
  220 passed (`/tmp/telescreen-svc-p06-composition-pytest-review-rerun.log`). Format,
  Ruff and ty gates exited 0 with logs `/tmp/telescreen-svc-p06-composition-format-review-rerun.log`,
  `/tmp/telescreen-svc-p06-composition-ruff-review-rerun.log` and
  `/tmp/telescreen-svc-p06-composition-ty-review-rerun.log`.
