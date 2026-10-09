# Python private database Police

This example runs next to a private SQLite database and connects outbound to the
Reporting Bureau in pull mode. It reads `display_locator` values in the form
`sqlite://<table>/<column>`, checks identifiers before constructing a query, and sends
only masked findings and keyed fingerprints.

Set `TELESCREEN_URL`, `TELESCREEN_POLICE_ENROLLMENT_TOKEN`, `PRIVATE_DB_PATH`, and
`TELESCREEN_FINGERPRINT_KEY` in the process environment. The enrollment token and
fingerprint key are secret references supplied by the deployment environment; do not
put credentials in this example or its configuration files. Install the Telescreen
package, then run `python -m examples.police.python.private_db_police` from the repository
root or run the file directly in an environment where the package is installed.

The worker registers the `verify_remediation` task type, claims tasks, heartbeats active
work, and submits masked results. Use a dedicated least-privilege SQLite account/file
and rotate the fingerprint key according to local policy.
