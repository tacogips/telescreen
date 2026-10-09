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
