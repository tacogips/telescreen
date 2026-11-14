# n8n push Police

Import `telescreen-push-police.json` into n8n and set
`TELESCREEN_POLICE_SIGNING_SECRET` through an n8n credential or protected environment
variable. Configure the workflow URL as the worker's HTTPS `endpoint_url`; register a
manifest with `mode: push` and the `verify_remediation` or `scan_shard` task type.

The signing input is the exact raw request body prefixed by
`<timestamp>.<nonce>.`; compare the resulting HMAC-SHA256 signature in constant time,
reject timestamps outside 300 seconds, and retain nonces for replay protection. The
included nodes show the verification and callback shape; adapt the Crypto node to include
the timestamp and nonce in its signing expression for the n8n version in use. Store the
result token only for the one callback. Never put a real secret in the imported workflow.

For `scan_shard`, return a count-only `stats` object and list every fully scanned resource
in `complete_locators`. If any enumerated resource fails, increment `resources_failed`;
the Bureau then skips removal reconciliation for the incomplete sweep. Any finding sent
to the Bureau must already have a masked snippet and a keyed external fingerprint.
