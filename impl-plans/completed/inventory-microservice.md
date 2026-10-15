# Inventory microservice extraction

Status: COMPLETED
Design: [Inventory microservice](../../design-docs/specs/design-inventory-microservice.md)
Created / updated: 2026-10-15

## Scope

Separate deployment and runtime ownership, building on the existing independently owned
stores and durable handoffs. Preserve public APIs and existing standalone mode.

## Workstreams

### 1. Versioned HTTP boundary

Deliverables: `inventory_service` typed transport, clients and internal endpoints.
Status: COMPLETED
Validation: authenticated API, invalid receipts, redirects, compression and outage/replay tests pass.

- [x] Implement authenticated typed HTTP adapters for existing session-free ports.
- [x] Keep identity, audit and notification receivers in the bureau.

### 2. Independent inventory runtime

Deliverables: standalone app, outbox poller, CLI and remote bureau composition.
Status: COMPLETED
Validation: real TCP processes and restricted PostgreSQL-owner HTTP flows pass.

- [x] Build inventory without main database or bureau service instances.
- [x] Proxy public API and wire producer delivery to HTTP.
- [x] Poll durable inventory outboxes and publish overdue obligations independently.

### 3. Deployment and verification

Deliverables: Compose example, README/runbook, service regression tests.
Status: COMPLETED
Validation: TCP smoke and Compose/schema checks pass; full PostgreSQL CI (1,314 passed / 6 skipped), web CI (262 passed), schema, build and wheel smoke pass.

- [x] Document migrations, cutover, configuration and replacement contract.
- [x] Verify outage recovery, permissions, replay and independent process operation.
- [x] Run quality gates and audit the full objective.

## Status table

| Workstream | Status | Validation |
| --- | --- | --- |
| HTTP boundary | COMPLETED | Typed authenticated HTTP and fault/replay checks |
| Runtime | COMPLETED | SQLite, PostgreSQL and TCP processes |
| Deployment and verification | COMPLETED | Full backend/web/schema/build and wheel checks pass |

## Dependencies

Existing inventory ownership migrations and transfer tools are prerequisites. HTTP
contracts precede remote composition; assembled runtime precedes deployment validation.

## Completion criteria

- [x] Inventory runs independently with only its own database credentials.
- [x] Collection and notification functionality use authenticated remote contracts.
- [x] Existing public APIs and permissions remain compatible.
- [x] Retry/replay survives process and network failure without duplicate effects.
- [x] Deployment instructions and verification gates pass.

## Progress log

### 2026-10-15

Inspected clean `feat/data-inventory`, checked out `main` and fast-forwarded it to the
existing inventory implementation. Confirmed previous work explicitly excluded network
service extraction; this plan addresses that remaining objective.

### 2026-10-15, service and deployment verification

Added the dedicated `telescreen-inventory` executable and lazy local composition so
neither process imports the other owner's business implementation at startup. HTTP
flows pass with SQLite and separate restricted PostgreSQL roles, including collection
outage, lost acknowledgments, audit/retention recovery and original route permissions.
Two real TCP CLI processes passed mutation/read and background audit delivery. The first
TCP attempt timed out during startup; captured logs and the unchanged-deadline rerun
verify startup and clean shutdown. Compose config and all 10 canonical schema checks
pass. Full PostgreSQL-enabled CI and web CI are running.

### 2026-10-15, final completion audit

Final PostgreSQL-enabled `PYTEST_ADDOPTS=-q mise run ci` passed: format, Ruff,
typelint (710 files), ty, 1,314 tests passed / six external-service skips, sdist and
wheel. Web CI passed all 262 tests, typecheck and production build. Canonical schema
and both exports match; all 10 schema tests pass. Compose validates and the extracted
wheel runs the inventory-only executable and migration outside the checkout. The
public export proxy streams without the internal handoff size limit. All 18 example
checks pass after correcting the documented password placeholder to the existing
`changeme` convention. No assertions or deadlines were weakened. The final
[completion audit](../../design-docs/specs/design-inventory-microservice-verification.md)
covers the entire original objective.
