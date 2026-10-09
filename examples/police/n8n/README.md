# n8n push Police

Import `telescreen-push-police.json` into n8n and set
`TELESCREEN_POLICE_SIGNING_SECRET` through an n8n credential or protected environment
variable. Configure the workflow URL as the worker's HTTPS `endpoint_url`; register a
manifest with `mode: push` and the `verify_remediation` or `scan_shard` task type.

The `Verify HMAC` Code node reads the exact Webhook raw body from binary property `data`,
then signs `<timestamp>.<nonce>.` followed by those bytes. It requires
`NODE_FUNCTION_ALLOW_BUILTIN=crypto` for `require('crypto')`; keep the signing secret in a
protected environment variable or n8n credential. The node checks the `v1=<hex>` signature
in constant time, rejects timestamps more than 300 seconds from the current time, and
stores accepted nonces in workflow global static data for 600 seconds. The IF node accepts
only requests whose signature, timestamp, nonce and raw body pass those checks.

Both directions use the registration response's `signing_secret` string's UTF-8 bytes
directly as the HMAC key; never base64-decode that string. The `Scan locally` Code node is
a safe placeholder: replace it with local verification logic that never returns source
values. It serializes a result envelope once as `resultBody`, with `result_token` inside
that body, and creates a fresh timestamp and nonce. `Sign callback` computes a hex HMAC
over `<timestamp>.<nonce>.` plus that exact string; `Submit signed result` posts the same
string with `X-Telescreen-Timestamp`, `X-Telescreen-Nonce`, and
`X-Telescreen-Signature: v1=<hex>`. The callback does not use an Authorization header.
Never put a real secret in the imported workflow.

For `scan_shard`, return a count-only `stats` object and list every fully scanned resource
in `complete_locators`. If any enumerated resource fails, increment `resources_failed`;
the Bureau then skips removal reconciliation for the incomplete sweep. Any finding sent
to the Bureau must already have a masked snippet and a keyed external fingerprint.
