# Local Compose deployment

This stack runs PostgreSQL, Meilisearch, the Reporting Bureau API, the workflow engine
worker, and the example Police process. The Dockerfile builds the SolidJS UI and Python
wheel before producing a small non-root runtime image.

## Start the stack

From this directory, copy the environment template and create the referenced secret files:

```sh
cp .env.example .env
mkdir -p secrets
printf '%s' 'replace-with-a-random-key-of-at-least-32-bytes' > secrets/fingerprint_key
printf '%s' 'replace-with-a-random-signing-key' > secrets/police_signing_key
printf '%s' 'replace-with-an-enrollment-token' > secrets/police_enrollment_token
printf '%s' 'replace-with-a-random-police-fingerprint-key' > secrets/police_fingerprint_key
export POLICE_ENROLLMENT_TOKEN="$(cat secrets/police_enrollment_token)"
export POLICE_FINGERPRINT_KEY="$(cat secrets/police_fingerprint_key)"
```

Compose mounts these files at the `/run/secrets/` paths already referenced by `.env`.
Replace the database and Meilisearch placeholders with local-only values, then start the
services:

```sh
docker compose up --build
```

The Reporting Bureau API and UI are available at <http://localhost:8000>. After creating
the administrator token below, enter it in the UI's Settings page.

## Create an administrator token

In another terminal, create the first bearer token and store the printed value securely:

```sh
docker compose run --rm bureau telescreen token create --label admin --scopes admin
```

The token is printed once. Do not paste it into `.env`, source control, or logs.

## Enroll the example Police

The Bureau reads its enrollment token from the mounted file reference. The exported
`POLICE_ENROLLMENT_TOKEN` passes the same file value to the example Police, which connects
to `http://bureau:8000` and registers for its supported verification tasks. The development
template enables HTTP only for this local example; use HTTPS endpoints and disable that
setting in a deployed environment.

The example Police has its own fingerprint key. The `POLICE_FINGERPRINT_KEY` export passes
the key value to that process; it is separate from the Bureau's mounted fingerprint key.

Slack and GitHub webhooks return HTTP 401 until `TELESCREEN_SLACK_SIGNING_SECRET` and
`TELESCREEN_GITHUB_WEBHOOK_SECRET` are configured as secret references. For each
configured `file:/run/secrets/<name>` reference, create the matching file under `secrets/`,
declare it under the Compose `secrets:` section, and mount it on the service that handles
the webhook. The template leaves both webhook secrets unset.

## v1.1 settings

The `.env.example` includes commented placeholders for internal domains, owner routing,
health recipients, and the weekly owner digest. Fingerprint key rotation is shown with
`file:` secret references; keep version 1 configured because it remains the identity key.
See [`design-docs/specs/command.md`](../../design-docs/specs/command.md) for all
`TELESCREEN_*` settings and defaults.

## Local test services (dev-services.yml)

The separate test-service stack supplies Mailpit, Keycloak, and PostgreSQL without starting
the Bureau or Meilisearch. Run it from the repository root:

```sh
docker compose -f examples/compose/dev-services.yml up -d
docker compose -f examples/compose/dev-services.yml down
```

Mailpit accepts SMTP on `127.0.0.1:1025` and shows messages at
<http://127.0.0.1:8025>. Keycloak is at `http://127.0.0.1:8080`; use that same
`127.0.0.1` host in the browser, Telescreen settings, and issuer URL because the token
issuer must exactly match the configured issuer. The imported `telescreen` realm has
`alice` (admin), `bob` (responder), `carol` (viewer), and `dave` (no mapped role); the
development-only passwords follow `<user>-dev-pass`.

For a local API/worker run, configure the database and OIDC settings in the same shell:

```sh
export TELESCREEN_DATABASE_URL=postgresql+asyncpg://telescreen:telescreen-dev@127.0.0.1:55432/telescreen
export TELESCREEN_OIDC_ISSUER=http://127.0.0.1:8080/realms/telescreen
export TELESCREEN_OIDC_CLIENT_ID=telescreen-ui
export TELESCREEN_OIDC_AUDIENCE=telescreen-ui
export TELESCREEN_OIDC_PROVISIONING=create
export TELESCREEN_OIDC_TEAM_MAP=security=secops,dev=dev,ghe-admin=ghe-admin
telescreen migrate
```

Start `telescreen serve` in one terminal and create the teams with an administrator token:

```sh
export TELESCREEN_ADMIN_TOKEN='<administrator token>'
for team in secops dev ghe-admin; do
  curl --fail http://127.0.0.1:8000/api/teams \
    -H "Authorization: Bearer $TELESCREEN_ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"slug\":\"$team\",\"name\":\"$team\"}"
done
```

Then run `telescreen worker` in another terminal. SMTP is configured with host
`127.0.0.1`, port `1025`, and STARTTLS disabled. No SMTP password is required.

Slack test credentials remain outside the repository. Source the owner's local test file
and pass references to Telescreen; the application reads the referenced environment values
only when sending:

```sh
set -a
. ~/.config/telescreen/slack-test.env
set +a
export TELESCREEN_SLACK_BOT_TOKEN=env:TELESCREEN_TEST_SLACK_BOT_TOKEN
telescreen notify test --channel slack --to "slack_channel:${TELESCREEN_TEST_SLACK_CHANNEL}"
telescreen notify test --channel email --to email:someone@example.test
```

Live tests are opt-in. Set `TELESCREEN_TEST_MAILPIT_URL`,
`TELESCREEN_TEST_SMTP_HOST`, `TELESCREEN_TEST_SMTP_PORT`,
`TELESCREEN_TEST_KEYCLOAK_URL`, `TELESCREEN_TEST_SLACK_BOT_TOKEN`, and
`TELESCREEN_TEST_SLACK_CHANNEL` for the corresponding services, then run:

```sh
uv run pytest -m live tests/live
```

Without these variables the live tests skip. The test suite does not read the local Slack
credential file itself.
