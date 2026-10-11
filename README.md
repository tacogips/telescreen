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

## Features (v1.1)

- Exposure-aware severity uses placement and visibility, including the new `critical` level.
- Versioned placement policies allow or flag findings and support the `max_age_days` condition.
- Verified resolution moves issues to `pending_verification`; a rescan decides, and policy changes never count as remediation.
- Scan coverage records scanned, partial, unsupported, denied, and failed resources; unscannable resources remain visible.
- Owner routing assigns work for `owner_review`, weekly digests, and the My tasks page.
- Collector and Police health report stale sources, failing scans, expiring watches, and silent workers.
- Linked occurrences connect the same keyed fingerprint across resources with fingerprint key versioning.
- Risk scores prioritize issues and support sorting and escalation with `min_risk`.
- Exceptions record a reason and approver, expire on schedule, and can reopen issues.
- Stale-data policies evaluate whether sensitive content exceeds a configured age.

## Features (v1.2)

- Versioned, editable notification templates are validated, previewed, and activated from Settings.
- Workflow v2 notification sets pair recipients with Slack or email channels; delivery outcomes are recorded per target.
- Admin, responder, and viewer roles control API and UI permissions, with member-bound token permissions intersected with the assigned role.
- OpenID Connect sign-in supports Authorization Code + PKCE, Keycloak role mapping, and an optional static-token login fallback.
- `telescreen notify test` exercises the production notification service and delivery handler; opt-in Mailpit, Slack, and Keycloak tests are under `tests/live/`.

See the [v1.2 design](design-docs/specs/design-v1-2-notifications-and-identity.md) for the permission matrix, configuration, and operator walkthrough.

Telescreen does not check provider credential validity or reveal raw values (it links to
the source location only), and it remains read-only on every monitored source.

## Data handling

Monitored content is scanned in memory and is not stored as a source copy. Raw detected
values are not persisted, logged, indexed, sent in notifications, or returned by APIs.
Stored findings use masked text and keyed HMAC fingerprints. Search indexing and outbound
notifications pass their respective data-loss prevention guards. Secret settings use
`env:NAME` or `file:/path` references; secret values are resolved only when needed.
Evidence is stored only after scanning accepts it. New exposure, owner, coverage, policy,
health, and occurrence records contain only enums, counts, timestamps, keyed references,
identifiers, operator configuration, and redacted text; they do not retain source copies,
file names, email addresses, or raw values.

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
| `telescreen token create --label TEXT --scopes read,write [--member HANDLE]` | Create an API token, optionally bound to a member |
| `telescreen notify test --channel email\|slack --to RECIPIENT [--template NAME]` | Send a DLP-gated test notification |
| `telescreen member set-role HANDLE admin\|responder\|viewer\|none` | Assign or recover a member role |
| `telescreen search reindex` | Rebuild guarded search indexes |
| `telescreen mcp` | Run the Assistant MCP server over stdio |

The `telescreen` executable and `python -m telescreen` are equivalent entry points.

## API

The REST API is rooted at `/api`. It provides ingestion, issues, workflow definitions and
runs, Police worker registration and task operations, evidence, audit search, and
Assistant chat. v1.1 adds issue policies and exceptions, linked occurrences, scan coverage,
collector and Police health, directory identities and team leads, and owner-review task
completion fields. The Police contract is published at `/api/police/v1/openapi.json` with
versioned JSON schemas. See the v1.1 endpoint table in `design-docs/specs/design-api-and-ui.md` and
`design-docs/specs/design-police-contract.md` for the contract details.

v1.2 adds `/api/notification-templates` (list, get, validate, preview, create version,
activate), `/api/users`, `/api/roles` and `PUT /api/users/{handle}/role`, `/api/tokens`
(list, create, revoke), and `/api/auth/config`, `/api/auth/session` and `/api/auth/me` for
OIDC sign-in. Every route declares one permission, and a route-walk test fails on any
undeclared route. A bearer credential is either a static API token or an IdP access token;
see the "v1.2 endpoints" section of `design-docs/specs/design-api-and-ui.md`.

## Web application

The SolidJS application is in `src/web`. Install dependencies and run the frontend
verification sequence with:

```bash
mise run web-ci
```

For development, run `mise run web-install` and then `cd src/web && bun run dev`.
Vite proxies API requests to `http://localhost:8000`; start the Bureau there when working
against live data. To render every page with masked local fixtures and no backend, run
`cd src/web && VITE_TELESCREEN_MOCK=1 bun run dev`. The mock data contains masked values.
Run `cd src/web && bun run screenshots` for the screenshot smoke against the mock API
(every page in light and dark themes plus the mobile Issues page, 43 images); it needs Chrome/Chromium or `TELESCREEN_SCREENSHOT_BROWSER` and writes images under
`tmp/ui-screenshots/`. The visual reference is
[`design-docs/specs/design-ui-sentry.md`](design-docs/specs/design-ui-sentry.md). The
self-hosted Rubik and Roboto Mono fonts use the OFL licenses in `src/web/public/fonts/`;
Lucide icons use ISC licensing.

The v1.1 My tasks page brings together owner-review tasks, exception approvals, and issues
assigned to the current member. The Policies page validates, versions, and activates
placement policies.

In v1.2 the login page offers SSO through the configured OIDC issuer (Authorization Code
+ PKCE, returning to `/auth/callback`) and, when `TELESCREEN_UI_TOKEN_LOGIN` allows it,
the static-token login. Settings has Notification templates (editor, validation, preview,
and version activation) and Users and roles. The workflow definition view shows the
notification sets of v2 workflows. Navigation and actions follow the signed-in member's
role.

To serve a production build locally, run `cd src/web && bun run build`, then set
`TELESCREEN_WEB_DIST=src/web/dist` when running `telescreen serve`. The directory must
contain `index.html`; otherwise startup exits with code 3. The Python wheel does not
include the separately built UI assets.

## Configuration

The v1.1 `TELESCREEN_*` settings for exposure, owner routing, digests, health alerts, risk
weights, and fingerprint key rotation are listed in
[`design-docs/specs/command.md`](design-docs/specs/command.md). To rotate fingerprint keys,
add `2=<secret-ref>` to `TELESCREEN_FINGERPRINT_KEYS`, set
`TELESCREEN_FINGERPRINT_ACTIVE_VERSION=2`, and keep version 1 configured because it
remains the identity key for resources, placements, and owners.

The v1.2 OIDC settings (`TELESCREEN_OIDC_ISSUER`, `TELESCREEN_OIDC_CLIENT_ID`,
`TELESCREEN_OIDC_AUDIENCE`, `TELESCREEN_OIDC_ROLE_MAP`, `TELESCREEN_OIDC_DEFAULT_ROLE`,
`TELESCREEN_OIDC_TEAM_MAP`, `TELESCREEN_OIDC_PROVISIONING` and the others) and
`TELESCREEN_UI_TOKEN_LOGIN` are also in `command.md`. Without a mapped role and without a
default role, an SSO user gets HTTP 403 (`no_role`).

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

## Local compose environment

The quick-start deployment is under `examples/compose/`. Follow
[`examples/compose/README.md`](examples/compose/README.md), or start it from the repository
root with `docker compose -f examples/compose/docker-compose.yml --env-file examples/compose/.env up --build`.
The bureau serves the built UI from `/app/web`. The bureau and engine share accepted
evidence through the `evidence-data` named volume, mounted at `/app/var/evidence` and
owned by the non-root image user. The example Police uses its own fingerprint key,
supplied through `POLICE_FINGERPRINT_KEY`. Slack and GitHub webhooks return HTTP 401 until `TELESCREEN_SLACK_SIGNING_SECRET` and
`TELESCREEN_GITHUB_WEBHOOK_SECRET` are configured as secret references and the matching
secret files are declared and mounted in Compose. Drive channel tokens are derived from
the fingerprint key; there is no separate Drive channel variable. Example
incident-response workflows are in `examples/workflows/`; `notification-sets.yaml` is a
`telescreen.workflow/v2` example with named notification sets.

`examples/compose/dev-services.yml` starts the local test services (Mailpit, Keycloak with
the `telescreen` realm and users alice, bob, carol and dave, and PostgreSQL). The setup for
running Telescreen against them, signing in through Keycloak, and sending test
notifications is in the "Local test services" section of `examples/compose/README.md`.

## Testing and checks

Run the Python format, lint, typing, test, and build gates with:

```bash
mise run ci
```

The end-to-end flows live in `tests/e2e`; the raw-value leak matrix over every sink lives
in `tests/leak`. The v1.1 flows (`tests/e2e/test_flow_v11_*.py`) cover placement policies,
verified resolution, owner routing, health, exceptions and fingerprint key rotation, and
`tests/leak/test_leak_matrix_v11.py` extends the leak matrix to the v1.1 records, so that
raw samples, file names and owner emails never reach a sink. The v1.2 flows
(`tests/e2e/test_flow_v12_notifications.py` for templates and notification sets, and
`tests/e2e/test_flow_v12_identity.py` for roles and OIDC sign-in) and
`tests/leak/test_leak_matrix_v12.py` cover templates, notification sets, role changes, API
tokens, OIDC sign-ins and the notify-test path, so that raw samples, tokens and secrets
never reach a sink. Live delivery and sign-in tests for Mailpit, Slack and Keycloak are in
`tests/live`, marked `live`, and run only when their `TELESCREEN_TEST_*` variables are set
(see `examples/compose/README.md`). The default test suite is
offline and uses fakes or in-memory SQLite. Tests requiring
PostgreSQL or Meilisearch are marked `integration` and run only when
`TELESCREEN_TEST_DATABASE_URL` or `TELESCREEN_TEST_MEILI_URL` is set. Frontend checks are
available through `mise run web-ci`. The headless screenshot smoke (`cd src/web && bun run
screenshots`, needs a local Chrome or Chromium) builds the UI, renders its pages against
the mock API into `tmp/ui-screenshots`, and fails on any browser error.
