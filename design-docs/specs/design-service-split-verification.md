# Service split verification evidence

This table maps the accepted R1-R8 requirements to implementation and the verification
available in continuation run 9. It distinguishes component evidence from the p08
multi-process acceptance flows. Run 7 command results below are historical and are not
evidence for the run 9 source.

| Requirement | Evidence | Result |
| --- | --- | --- |
| R1: Inventory independence and directory-backed auth | `tests/inventory_service/test_runtime.py`, `test_split_inventory.py`; `tests/split/test_replay_process.py::test_inventory_outage_replays_once`; `tests/inventory_service/test_process.py::test_three_processes_serve_from_three_owned_databases` | Three service processes plus the Telescreen worker start on separate SQLite stores. Inventory targets the live Notification TCP port. Both replay cases pass in `/tmp/telescreen-svc-p08-verification-deploy-g9-replay-final-source.log` (2 passed, 0 skipped). OIDC parity and token revoke/replay remain outside this plan. |
| R2: independently stored Notification service | `tests/notification_service/test_app_auth.py`, `test_app_routes.py`, `test_worker.py`; three-process health smoke | Component tests and process startup pass. Notification command application and delivery are not yet asserted across the process boundary. |
| R3: durable notification commands and receipts | `tests/notification_service/test_inbox.py`, `test_prior_hits.py`, `test_dlp_parity.py`; `tests/split/test_replay_process.py::test_notification_outage_replays_once` and `::test_inventory_outage_replays_once` | The current-source replay suite passes (2 passed, 0 skipped) in `/tmp/telescreen-svc-p08-verification-deploy-g9-replay-final-source.log`. It verifies the mention command receipt, duplicate replay, and Inventory stream sequence. The 3x full process gate remains incomplete because cutover and leak-matrix gates fail. |
| R4: notification settings/templates and permissions | `tests/e2e/test_wiring_split.py`, `tests/notification_service/test_settings_router.py`, `tests/e2e/test_route_permissions.py` | Covered by the passing p08 scoped suite in-process. Split proxy permission parity is not covered by the new TCP smoke. |
| R5: overdue delivery while Telescreen is stopped and cutover deduplication | `tests/inventory_service/test_runtime.py`; `tests/split/test_cutover.py::test_notification_cutover_copies_pending_row_and_retention_receipt`; `tests/split/test_stopped_telescreen_process.py::test_inventory_serves_and_delivers_with_telescreen_stopped`; `tests/split/test_cutover_process.py::test_notification_cutover_process_delivers_once_and_deduplicates_retention` | The stopped-Telescreen process test passes from run 8. The new process cutover test shows that a copied `NotificationRow(status='pending')` is not delivered: Notification's pump schedules only `queued` rows. Fixing this requires notification-service behavior outside svc-p08 writePaths; a Reconciliation request is recorded in the plan. |
| R6: restricted PostgreSQL logins | `tests/inventory_service/test_postgres.py::test_three_owner_roles_cannot_read_other_stores_or_migration_state` | Integration test is implemented and marked `integration`; skipped because `TELESCREEN_TEST_DATABASE_URL` is not configured. No live grant result is claimed. |
| R7: import gates and leak matrix | `tests/split/test_import_gates.py`; `tests/leak/flows_split.py`; `tests/leak/test_leak_matrix_split.py::test_split_process_leak_matrix` and `::test_split_leak_matrix_fails_when_owned_table_is_missing` | The matrix now runs a ServiceStack flow through ingest, comment/mention, workflow notify, rejected locator and context-field probes; drains producer outboxes and projection markers; exports both service audits; and scans all three owned table sets, four process logs, API bodies and SMTP data for raw and JSON-escaped samples. The missing-table negative test passes. The non-vacuous producer-redaction assertion currently fails because the workflow executor redacts inline text before publishing, leaving all observed `ProducerRedactionHits` at zero. See `/tmp/telescreen-svc-p08-verification-deploy-g9-replay-leak-rerun.log`; this requires an in-scope way to produce that carrier or a plan reconciliation. |
| R8: deployable three-store example | `examples/compose/docker-compose.services.yml`; `tests/examples/test_examples.py::test_three_service_compose_has_disjoint_stores_and_private_services` | YAML topology, ports, channel variable placement and placeholder checks pass. Compose containers were not started. |

## Commands and results

| Command | Result |
| --- | --- |
| `uv run --frozen ruff format --check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` | Exit 0; 68 files already formatted. |
| `uv run --frozen ruff check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` | Exit 0; all checks passed. |
| `uv run --frozen ty check tests/split tests/inventory_service tests/e2e tests/leak tests/examples` | Exit 0; all checks passed. |
| `uv run --frozen python tools/typelint.py` with p08 path filter | Exit 0; no diagnostics in p08 paths. |
| `uv run --frozen pytest -p no:cacheprovider tests/split tests/inventory_service tests/notification_service tests/leak tests/examples tests/e2e` | Exit 0; 175 passed, 4 environment-gated integration tests skipped. Review-repair source log: `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-pytest.log`. |
| `uv run --frozen ruff format --check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` | Exit 0; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-format.log`. |
| `uv run --frozen ruff check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` | Exit 0; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-ruff.log`. |
| `uv run --frozen ty check tests/split tests/inventory_service tests/e2e tests/leak tests/examples` | Exit 0; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-ty.log`. |
| `uv run --frozen python tools/typelint.py` | Exit 0; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-typelint.log`. |
| `mise run ddl-check` | Exit 0; Nodetto exports canonical and 10 schema tests passed; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-ddl.log`. |
| `mise run web-ci` | Exit 0; typecheck, 262 tests and production build passed; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-web.log`. |
| `uv run --frozen pytest -p no:cacheprovider -q tests/inventory_service/test_process.py tests/leak/test_leak_matrix_split.py` | Exit 0; 2 passed. Review-repair log: `/tmp/telescreen-svc-p08-g7-r3-process-leak.log`. |
| `uv build --out-dir /tmp/telescreen-svc-p08-verification-deploy-g7-r3-dist` | Exit 0; source distribution and wheel built; `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-build.log`. |
| `mise run ci` | Exit 0; 1397 passed, 35 skipped, 1 warning in the full Python suite; format, Ruff, typelint, `ty` and package build passed. Review-repair log: `/tmp/telescreen-svc-p08-verification-deploy-g7-r3-ci.log`. |

The service-split implementation and deployment artifacts are present. This p08 evidence
does not certify the full R1-R8 cross-process matrix: producer outage/replay, DLP delivery
parity, stopped-Telescreen overdue delivery, complete notification-first cutover, all-sink
leak scanning and live PostgreSQL grants remain unverified. Live service checks remain
owner-run as specified by the accepted design.

Run 8 implementation evidence is partial. The new stopped-Telescreen process test exposed
an Inventory directory delivery gap after the producer outbox emitted sequences 2-5; the
consumer cursor stopped at sequence 3. See the svc-p08 Progress Log and
`/tmp/p08-stopped-process-attempt4.log`. No production fix is within svc-p08 ownership.
