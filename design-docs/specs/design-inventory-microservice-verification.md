# Inventory microservice completion audit

Status: VERIFIED
Date: 2026-10-15
Design: [Inventory microservice](design-inventory-microservice.md)
Plan: [Extraction plan](../../impl-plans/completed/inventory-microservice.md)

## Requirements and current evidence

| Requirement | Authoritative evidence | Result |
| --- | --- | --- |
| Work on `main` without discarding existing inventory work | Clean initial feature branch, `git checkout main`, fast-forward from `fdd02a6` to `3cf1d36`; current branch is `main` at merge commit `b49dffe` (same tree as `3cf1d36`) | PASS |
| Inventory runs as a separate microservice | Dedicated packaged `telescreen-inventory serve` / `python -m telescreen.inventory_service` entry point; `app.py` builds only inventory-owned runtime; real two-process TCP mutation/read/background audit test | PASS |
| Inventory owns its database independently | `build_inventory_runtime` requires only inventory URL and creates only that engine; separate owner migrations; inventory-only CLI migration test and extracted-wheel migration create inventory tables with no main tables or main URL | PASS |
| Collection uses a replaceable remote contract | Remote composition binds the existing publisher/outbox to `InventoryClient`; collection outage test commits the issue while inventory is down, then recovers a lost reply with one asset and an acknowledged producer outbox | PASS |
| Notification functionality remains separate | Inventory only publishes retention obligations; bureau constructs `RetentionNotificationReceiver` and owns recipients/policy/notification queue; remote eligibility lookup crosses HTTP, never a shared SQL transaction | PASS |
| Audit and retention are durable across outages/replay | Inventory-local outbox pumps acknowledge only validated remote receipts; callback outage/lost-reply test verifies pending rows, one notification and a valid audit chain | PASS |
| Existing public inventory functionality and authorization remain available | Existing routers mounted in inventory; bureau streams `/api/data/*`; complete original 22-route permission table verified; tests cover missing auth, read-only denial, revocation, creation/detail/enrichment/CSV and large public exports beyond the internal message bound | PASS |
| Future inventory changes do not require importing its implementation into the bureau | Lazy local composition, neutral `WiringV14Parts`, existing session-free ports and typed HTTP clients; isolated-process import gates reject inventory imports by bureau and bureau business imports by inventory executable | PASS |
| Real PostgreSQL ownership boundary works with HTTP | Three assembled flows run through HTTP using configured production pools and separately migrated restricted PostgreSQL login owners; wrong-owner SQL fails | PASS |
| Service boundary validates authentication and transport results | Shared-secret dependencies/middleware, delegated bearer verification, fixed-origin URLs, finite timeouts, redirect refusal, bounded internal payloads, typed codecs/responses; malformed/redirect/compressed-response tests pass | PASS |
| Independently deployable example and migration/cutover instructions | Compose overlay has a distinct PostgreSQL instance, inventory migration and inventory-only process environment; merged Compose config validates; design/README/command docs cover secrets, rollout, rollback and adapter contract | PASS |
| Required final checks and package build pass on current sources | Final format/lint/typelint/ty, full PostgreSQL-enabled backend CI, schema checks, web CI, wheel smoke and diff/size inspection | PASS |

The focused final service suite passes **26 tests**, including the three PostgreSQL
flows, import gates, streaming export regression and real TCP processes. These tests
exercise owner-local migrations, API authorization, manual registration, collection,
issue enrichment, audit, retention, outage recovery and duplicate handling. Existing
owner migration/cutover/replay tests remain in the full suite; their schema and durable
command formats are unchanged.

## Execution evidence

| Command/check | Result | Local log |
| --- | --- | --- |
| PostgreSQL-enabled `uv run pytest -q tests/inventory_service` | 26 passed | `/tmp/telescreen-inventory-service-tests-final.log` |
| PostgreSQL-enabled `PYTEST_ADDOPTS=-q mise run ci` | Exit 0: format, Ruff, typelint (710 files), ty, 1,314 passed / 6 skipped, sdist and wheel | `/tmp/telescreen-inventory-microservice-ci.log` |
| `mise run ddl-check` | Canonical document/exports current, 10 passed | `/tmp/telescreen-inventory-service-ddl.log` |
| `mise run web-ci` | Typecheck, 49 files / 262 tests, production build passed | `/tmp/telescreen-inventory-microservice-web.log` |
| Merged Compose config with example interpolation and `--no-env-resolution --quiet` | Exit 0 | Reproducible with the documented two-file command and example environment |
| `uv build` and extracted-wheel entry-point/migration smoke | Passed on final code, including streaming correction | `/tmp/telescreen-inventory-microservice-build.log`, `/tmp/telescreen-inventory-service-wheel.log` |

The first TCP smoke hit its unchanged 30-second startup deadline; logging was added and
subsequent TCP runs passed with the same deadline. The first full backend CI was
intentionally interrupted after 246 passing tests when review identified that public
exports must stream without the internal 16 MiB limit. Its log is preserved at
`/tmp/telescreen-inventory-microservice-ci-before-streaming.log`; the final run above
includes that correction and its regression test. A later complete run finished with 1,313 passing tests and one example-template
failure: the added password placeholder was detected by the repository's password
rule. The template now uses the existing `changeme` convention; all 18 example tests
pass. That complete failure log is retained at
`/tmp/telescreen-inventory-microservice-ci-env-placeholder.log`; the complete final CI rerun passed
with the corrected template. No test assertion or timeout was weakened.

## Operating limits

Inventory API authentication and enriched reads depend on the bureau being available.
Collection can commit while inventory is unavailable, and already-committed inventory
outbox work survives bureau outages. Service calls are authenticated; deployment must
use private networking or HTTPS as documented. There is no distributed snapshot or
transaction. The existing one-hour overdue catch-up window and durable receipt retention
policy remain in effect. No production data was migrated or deployed during this work.

## Final outcome

Every requirement above is verified. The final complete backend run passed all 1,314
tests (six opt-in external-service skips), including PostgreSQL migration, ownership,
cutover and HTTP service tests. No requested implementation or verification remains
open. The temporary PostgreSQL instance was stopped after verification. No production
data was moved. The implementation and verification are complete on `main`; no deployment was performed.
