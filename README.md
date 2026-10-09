# Telescreen

Telescreen monitors systems for credentials, personal data, and other sensitive data
that should not be retained there. Collectors scan source content transiently; detectors
identify findings; masking removes raw values before the Reporting Bureau stores,
searches, reports, or notifies about them. Issues and durable workflows help teams
coordinate remediation and verification.

## Architecture

The Reporting Bureau is the Python service and owns configuration, the PostgreSQL-backed
issue and workflow state, search, audit, and APIs. Collector adapters read Slack, Google
Drive, Docs and Sheets, GitHub, and custom sources on events or schedules. A detector
pipeline returns finding spans; the masking boundary persists only masked summaries and
keyed fingerprints. The Bureau groups repeated findings into issues, indexes masked
records in Meilisearch, and drives durable workflow tasks. Police workers verify work
through a versioned HTTP and JSON contract. The SolidJS application uses the Bureau API.

The naming map keeps themed language to **Telescreen**, **Reporting Bureau**, and
**Police**. Internal package, module, endpoint, and UI names are plain descriptions such
as `telescreen.collectors`, `telescreen.detection`, `/api/issues`, and Issues.

## Data handling

Monitored content is scanned in memory and is not stored as a source copy. Raw detected
values are not persisted, logged, indexed, sent in notifications, or returned by APIs.
Stored findings use masked text and keyed HMAC fingerprints. Search indexing and outbound
notifications pass their respective data-loss prevention guards. Secret settings use
`env:NAME` or `file:/path` references; secret values are resolved only when needed.
Evidence is stored only after scanning accepts it.

## Setup

Install the pinned toolchain and Python development dependencies:

```bash
mise run setup
```

Configuration is supplied through environment variables. Names, defaults, secret
references, and service-specific required settings are listed in
[`design-docs/specs/command.md`](design-docs/specs/command.md). `workflow validate` can
run without database configuration; other services validate requirements when used.

## CLI

| Command | Purpose |
|---------|---------|
| `telescreen serve` | Run the Reporting Bureau API |
| `telescreen worker [--once]` | Process durable engine tasks and scheduled work |
| `telescreen migrate [--revision head]` | Apply database migrations |
| `telescreen workflow validate FILE...` | Validate workflow YAML or JSON offline |
| `telescreen collector run NAME [--watch]` | Enqueue a collector scan or register a supported watch |
| `telescreen collector sweep NAME` | Start a concurrent whole-estate sweep |
| `telescreen police list` | List registered Police workers |
| `telescreen token create --label TEXT --scopes read,write` | Create an API token |
| `telescreen search reindex` | Rebuild guarded search indexes |
| `telescreen mcp` | Run the Assistant MCP server over stdio |

The `telescreen` executable and `python -m telescreen` are equivalent entry points.

## API

The REST API is rooted at `/api`. It provides ingestion, issues, workflow definitions and
runs, Police worker registration and task operations, evidence, audit search, and
Assistant chat. The Police contract is published at `/api/police/v1/openapi.json` with
versioned JSON schemas. See `design-docs/specs/design-api-and-ui.md` and
`design-docs/specs/design-police-contract.md` for the contract details.

## Web application

The SolidJS application is in `src/web`. Install dependencies and run the frontend
verification sequence with:

```bash
mise run web-ci
```

For development, run `mise run web-install` and then `cd src/web && bun run dev`.

## Police workers

Police can use pull mode to register, claim tasks, heartbeat, and submit results, or push
mode with signed task delivery and callbacks. The standalone Python SDK lives in
`src/telescreen_police_sdk/`; an importable n8n workflow and setup instructions are under
`examples/police/`. Police integrations use the published contract and do not import
Bureau internals.

The Bureau side is implemented in `telescreen.police` with routes under
`/api/police/v1` (`workers/register`, `workers/{id}/claim`, `workers/{id}/heartbeat`,
`tasks/{id}/result`, and `callbacks/{id}`). Bureau components use a `PoliceGateway`
protocol with two transports: `PullQueueTransport` and `WebhookTransport`.

- Pull mode: registration returns a worker token, and the Bureau stores only its hash.
  Each claim long-polls, filters tasks by the worker's labels, and leases them.
- Push mode: deliveries and callbacks are signed with HMAC-SHA256. The key is the
  UTF-8 bytes of the registration `signing_secret`, used as-is without base64 decoding.
  The signed input is `<timestamp>.<nonce>.` followed by the raw body. The values go in
  the `X-Telescreen-Timestamp`, `X-Telescreen-Nonce`, and `X-Telescreen-Signature: v1=<hex>`
  headers.
- Replay protection: timestamps more than 300 seconds old are rejected, and nonces are
  remembered so reuse is rejected. Each delivery has a one-time `result_token` in its
  body. The token can be used only once, and the callback needs no Authorization header.
- Attempts: each claim or push POST counts as one attempt, up to `max_attempts` (default
  3). When a lease expires, the task is re-queued without using up an attempt. A task
  fails once its attempts or its deadline run out.
- Raw values: results go through schema validation and a scan of every string. A result
  containing a raw value is rejected with HTTP 422, and the task stays open, so the
  Police can resubmit a clean result before the deadline.

Registering the push-delivery engine task and mounting the Police router into the
assembled application belong to the assembly plan (`p28-assembly`).

## Local compose environment

The quick-start deployment is under `examples/compose/`. Start it with
`docker compose -f examples/compose/compose.yaml up --build`; configure credentials using
secret references and environment variables described in the command design. Example
incident-response workflows are in `examples/workflows/`.

## Testing and checks

Run the Python format, lint, typing, test, and build gates with:

```bash
mise run ci
```

The default test suite is offline and uses fakes or in-memory SQLite. Tests requiring
PostgreSQL or Meilisearch are marked `integration` and run only when
`TELESCREEN_TEST_DATABASE_URL` or `TELESCREEN_TEST_MEILI_URL` is set. Frontend checks are
available through `mise run web-ci`.
