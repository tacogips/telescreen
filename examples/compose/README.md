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
export POLICE_ENROLLMENT_TOKEN="$(cat secrets/police_enrollment_token)"
```

Compose mounts these files at the `/run/secrets/` paths already referenced by `.env`.
Replace the database and Meilisearch placeholders with local-only values, then start the
services:

```sh
docker compose up --build
```

The API and UI are available at <http://localhost:8000>.

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
