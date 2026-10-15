# Inventory microservice

Status: IMPLEMENTED, 2026-10-15

## Overview

Extract the existing inventory owner into a separately runnable HTTP service. The
inventory process owns only its ten inventory tables and migration history. The bureau
owns collection, issues, identity, audit, notification policy and delivery. Existing
in-process composition remains available for small installations.

## Details

`TELESCREEN_INVENTORY_SERVICE_URL` selects HTTP adapters in the bureau. In that mode it
must not open an inventory connection or construct inventory business services. Public
`/api/data/*` requests are forwarded with the original bearer credential. Inventory
asks the bureau to authenticate it through an internal versioned endpoint, then applies
the same route permissions locally. No caller-supplied principal headers are trusted.

`telescreen-inventory serve` builds only inventory services and its database engine. Issue and
identity enrichment use versioned HTTP lookups. Observations retain the existing
ordered, durable producer outbox and transactional inbox protocol. Inventory polls its
own audit/retention outboxes, delivers to bureau-owned receivers, and acknowledges only
a validated committed receipt. Lost replies replay the same event identity. A bounded
rotating poll continues past failures and restarts from durable rows after a crash.
Due-date publication runs in the inventory process; the bureau no longer schedules it.

Internal endpoints and proxied inventory requests require a shared secret resolved from
`TELESCREEN_INVENTORY_SERVICE_TOKEN`. Configure the same secret in both processes and
use private networking plus TLS when traffic leaves a trusted host. HTTP calls have a
finite timeout, reject redirects and validate typed responses. Remote failures return
safe errors; pending outbox records survive outages. The inventory service needs a
bureau callback URL (`TELESCREEN_BUREAU_SERVICE_URL`) and its own database URL, with no
main URL. Independent owner migrations and the existing stopped-writer transfer runbook
remain authoritative; no new tables are needed.

The versioned contract is `/internal/inventory/v1/*`; public API JSON and CSV remain
unchanged. Inventory adapters can be replaced behind the existing ports, or the service
can be reimplemented against this HTTP contract without changing collectors or the UI.

## Verification

Verify real separately owned stores through assembled ASGI applications, including API
permissions, mutation/read/export, observation delivery, audit/retention callbacks,
outages, duplicate receipts, malformed responses and unauthorized internal calls. Also
run a real separate-process HTTP smoke test, required Python gates, schema checks and
package build. Document deployment and adaptation procedures.

## Configuration and operation

| Process | Required settings | Command |
| --- | --- | --- |
| Bureau API and engine | Existing main settings, `TELESCREEN_INVENTORY_SERVICE_URL`, `TELESCREEN_INVENTORY_SERVICE_TOKEN` | `telescreen serve`, `telescreen worker` |
| Inventory | `TELESCREEN_INVENTORY_DATABASE_URL`, `TELESCREEN_BUREAU_SERVICE_URL`, `TELESCREEN_INVENTORY_SERVICE_TOKEN`, existing fingerprint key settings | `telescreen-inventory serve --port 8001` |
| Main migration | Main database URL only | `telescreen migrate --store main` |
| Inventory migration | Inventory database URL only | `telescreen-inventory migrate` |

Token settings contain an `env:NAME` or `file:/path` reference, using the existing secret
resolver. Use the same fingerprint key/version configuration in collectors and inventory.
Inventory reads `TELESCREEN_DATA_OVERDUE_CRON` and `TELESCREEN_PUBLIC_BASE_URL`; the bureau
ignores its local overdue scheduler in remote mode. Do not inject inventory sessions into
a remote bureau. A configured inventory URL is never connected by a remote bureau.

For a fresh local Compose deployment, copy the existing `.env.example`, populate the
existing secrets, set `INVENTORY_POSTGRES_PASSWORD`, and create
`examples/compose/secrets/inventory_service_token`. From `examples/compose` run:

```sh
docker compose -f docker-compose.yml -f docker-compose.inventory.yml up --build
```

The overlay creates a separate PostgreSQL instance, owner migration container and
inventory API/worker process. Inventory has no shared `.env` file, main database URL,
Slack token or SMTP credentials. Only the bureau publishes a host port. Each owner can
restart independently; the other owner retains durable pending work during outages.
Use separately provisioned DDL and runtime credentials as described in the existing
[cutover runbook](design-inventory-store-cutover.md) for production.

For existing data, stop all writers and follow the cutover runbook's checked adoption,
copy and verification steps. Keep inventory stopped while copying; start it with the
target URL and configure **both** bureau API and engine with the service URL/token
before restarting writers. Old inventory tables are retained only for controlled
rollback. To return to in-process operation, stop writers and the inventory service,
use the verified authoritative merge if returning to a shared store, remove the service
URL, and start the existing composition. Never run both local and remote inventory
writers against different copies of the same register.

The dedicated executable (`python -m telescreen.inventory_service` also works) imports
no bureau composition, collection, issue service or notification receiver implementation.
The bureau loads local inventory composition lazily only in in-process mode. The existing
`telescreen inventory-serve` command remains a compatibility alias. Inventory startup
never runs migrations implicitly. The authenticated
`GET /internal/inventory/v1/health` endpoint is process liveness. The process lifespan
starts three independent loops: due-date publication, audit delivery and retention
delivery. Outboxes retain pending work across restarts; repeated delivery uses the
existing durable receiver receipts. Delivery rotates batches of 20 entries with the
configured worker poll interval and a 30-second timeout per request. Current delivery
has no pruning or retry cap. Due-date scheduling preserves the existing one-hour
catch-up window. Public APIs still need bureau authentication/enrichment availability.
No distributed transaction or global snapshot is promised.

## HTTP contract and adapter replacement

All internal operations use JSON, the `x-telescreen-service-token` header, and the
`/internal/inventory/v1` prefix. Inventory public routes require the same service header
plus the original user `Authorization` header; the bureau proxy supplies the service
credential itself and discards caller-supplied service headers.

| Owner | Method/path suffix | Request | Response |
| --- | --- | --- | --- |
| Inventory | POST `/commands` | Existing version-1 `InventoryCommand` codec | `"applied"`, `"duplicate"`, `"gap"` |
| Inventory | POST `/retention-status` | Existing version-1 `RetentionCommand` codec | `RetentionStatus` |
| Bureau | POST `/authenticate` | `null`, original bearer header | Detached `Principal` |
| Bureau | POST `/issues` | Array of resource keys | Array of `ResourceIssueRef` |
| Bureau | POST `/actors` | Array of actor refs | Ordered array of `ActorDisplay` |
| Bureau | POST `/assignee` | Reference string | `AssigneeIds` |
| Bureau | POST `/audit` | Existing version-1 audit wire codec | `"applied"`, `"duplicate"` |
| Bureau | POST `/retention` | Existing version-1 `RetentionCommand` codec | `"applied"`, `"duplicate"`, `"superseded"` |

Dataclasses/enums in `ports/` and their validated codecs define the wire shapes. Clients
in `inventory_service/clients.py` implement existing ports; domain modules do not import
HTTP. Replace those adapters to use another transport. A replacement inventory service
must preserve public data responses, route permissions, command version validation,
ordered inbox/digest/replay semantics, and retention eligibility. Keep collector outbox
IDs/positions and receiver receipts through the replay window. Incompatible evolution
uses a new endpoint version with an explicit migration, rather than accepting unknown
command versions. Request and internal response bodies are bounded to 16 MiB. Public responses, including
CSV exports, stream through the proxy without that handoff limit. Redirects are never followed.

## Completion evidence

See the [requirement-by-requirement verification](design-inventory-microservice-verification.md)
for current-state implementation and command evidence.
