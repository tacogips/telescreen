# Service split verification evidence

This table maps the accepted R1-R8 requirements to implementation and the verification
available in continuation run 11. It distinguishes component evidence from the p08
multi-process acceptance flows. The command results below are from the run 11 re-verify
of checkpoint source `66531bd`.

| Requirement | Evidence | Result |
| --- | --- | --- |
| R1: Inventory independence and directory-backed auth | `tests/inventory_service/test_runtime.py`, `test_split_inventory.py`; `tests/split/test_replay_process.py::test_inventory_outage_replays_once`; `tests/inventory_service/test_process.py::test_three_processes_serve_from_three_owned_databases` | Three service processes plus the Telescreen worker start on separate SQLite stores. Inventory targets the live Notification TCP port. The focused process suite passed 7 tests with 0 skips in `/tmp/telescreen-svc-p08-verification-deploy-g11-process-1.log`, `-process-2.log` and `-process-3.log`. OIDC parity and token revoke/replay remain outside this plan. |
| R2: independently stored Notification service | `tests/notification_service/test_app_auth.py`, `test_app_routes.py`, `test_worker.py`; `tests/split/test_cutover_process.py::test_notification_cutover_process_delivers_once_and_deduplicates_retention` | Component tests and process startup pass. The process cutover test proves a copied queued member notification is delivered once over the standalone Notification service. |
| R3: durable notification commands and receipts | `tests/notification_service/test_inbox.py`, `test_prior_hits.py`, `test_dlp_parity.py`; `tests/split/test_replay_process.py::test_notification_outage_replays_once` and `::test_inventory_outage_replays_once` | The focused process suite passed 7 tests, 0 skipped in each of `/tmp/telescreen-svc-p08-verification-deploy-g11-process-{1,2,3}.log`. It verifies the mention command receipt, duplicate replay, Inventory stream sequence and cutover delivery. |
| R4: notification settings/templates and permissions | `tests/e2e/test_wiring_split.py`, `tests/notification_service/test_settings_router.py`, `tests/e2e/test_route_permissions.py` | Covered by the passing p08 scoped suite in-process. Split proxy permission parity is not covered by the new TCP smoke. |
| R5: overdue delivery while Telescreen is stopped and cutover deduplication | `tests/inventory_service/test_runtime.py`; `tests/split/test_cutover.py::test_notification_cutover_copies_pending_row_and_retention_receipt`; `tests/split/test_stopped_telescreen_process.py::test_inventory_serves_and_delivers_with_telescreen_stopped`; `tests/split/test_cutover_process.py::test_notification_cutover_process_delivers_once_and_deduplicates_retention` | The stopped-Telescreen and cutover process flows pass in all three g11 process logs (`/tmp/telescreen-svc-p08-verification-deploy-g11-process-1.log`, `-process-2.log` and `-process-3.log`). The copied row is queued with `member:alice`, reaches `sent` once, and remains sent after 5 seconds. The copied retention receipt suppresses a new notification. The documented cutover now resyncs both directory replicas and drains projection changes before stopping writers and copying notification state, preventing the reproduced `recipient_unknown` race. |
| R6: restricted PostgreSQL logins | `tests/inventory_service/test_postgres.py::test_three_owner_roles_cannot_read_other_stores_or_migration_state` | Integration test is implemented and marked `integration`; skipped because `TELESCREEN_TEST_DATABASE_URL` is not configured. No live grant result is claimed. |
| R7: import gates and leak matrix | `tests/split/test_import_gates.py`; `tests/leak/flows_split.py`; `tests/leak/test_leak_matrix_split.py::test_split_process_leak_matrix` and `::test_split_leak_matrix_fails_when_owned_table_is_missing` | The matrix runs ingest, comment/mention, workflow notify, rejected locator and context-field probes; drains producer outboxes and projection markers; exports both service audits; and scans all three owned table sets, four process logs, API bodies and SMTP data for raw and JSON-escaped samples. Non-vacuity is proven by a non-empty masked finding for the ingested issue and positive joined `scan_jobs.findings_count`; the missing-table negative test passes. Both tests pass in all three focused process runs. |
| R8: deployable three-store example | `examples/compose/docker-compose.services.yml`; `tests/examples/test_examples.py::test_three_service_compose_has_disjoint_stores_and_private_services` | YAML topology, ports, channel variable placement and placeholder checks pass. Compose containers were not started. |

## Commands and results

| Command | Result |
| --- | --- |
| `uv run --frozen ruff format --check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` | Exit 0; 72 files already formatted. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-format.log`. |
| `uv run --frozen ruff check --no-cache tests/split tests/inventory_service tests/e2e tests/leak tests/examples` | Exit 0; all checks passed. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-ruff.log`. |
| `uv run --frozen ty check tests/split tests/inventory_service tests/e2e tests/leak tests/examples` | Exit 0; all checks passed. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-ty.log`. |
| `uv run --frozen python tools/typelint.py` with p08 path filter | Exit 0; 790 files clean and no diagnostics in p08 paths. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-typelint.log`. |
| Focused 3x process loop: replay, stopped-Telescreen, cutover, leak matrix and Inventory process tests | Each run exited 0: 7 passed, 0 skipped. Logs: `/tmp/telescreen-svc-p08-verification-deploy-g11-process-{1,2,3}.log` (569.62s, 565.35s, 565.72s). |
| `uv run --frozen pytest -p no:cacheprovider tests/split tests/inventory_service tests/notification_service tests/leak tests/examples tests/e2e` | Exit 0; 180 passed, 4 environment-gated integration tests skipped. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-scoped.log`. |
| `mise run ci` | Exit 0; 1402 passed, 35 skipped, 1 warning; format, Ruff, typelint, `ty` and package build passed. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-ci.log`. |
| `mise run ddl-check` | Exit 0; Nodetto exports canonical and 10 schema tests passed. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-ddl.log`. |
| `mise run web-ci` | Exit 0; typecheck, 262 tests and production build passed. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-web.log`. |
| `! grep -nE 'mark\.skip|\.skip\(|skipif' <process modules>` | Exit 0; no matches. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-skips.log`. |
| `uv build --out-dir /tmp/telescreen-svc-p08-verification-deploy-g11-dist` | Exit 0; source distribution and wheel built. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-build.log`. |
| `! pgrep -f 'telescreen.(inventory_service|notification_service) serve'` | Exit 0; no service process survived. Log: `/tmp/telescreen-svc-p08-verification-deploy-g11-pgrep-final.log`. |
| `git diff --quiet HEAD -- src db tests examples` and the Python/plan line-limit checks | Exit 0; no code, DDL, test or example changes; all files remain within limits. Logs: `/tmp/telescreen-svc-p08-verification-deploy-g11-source-diff.log` and `/tmp/telescreen-svc-p08-verification-deploy-g11-line-limits.log`. |

The final offline p08 verification is complete. Restricted-login PostgreSQL and live
Mailpit/Keycloak checks remain owner-run because their service credentials are not
available to this verification environment. No production source, DDL or migration
files changed in svc-p08.
