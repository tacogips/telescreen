# Go Google Drive Police deployment

Configure the Bureau `TELESCREEN_POLICE_ENROLLMENT_TOKEN` using a secret reference, and provide the same secret to the worker through `TELESCREEN_POLICE_ENROLLMENT_TOKEN` or `TELESCREEN_POLICE_ENROLLMENT_TOKEN_FILE`. Supply `TELESCREEN_URL`, `TELESCREEN_POLICE_FINGERPRINT_KEY` (or its `_FILE` form), and `TELESCREEN_GOOGLE_SERVICE_ACCOUNT_FILE` through the deployment environment. Use a stable fingerprint key and protect the service account file. Optionally set `TELESCREEN_GOOGLE_DELEGATED_ADMIN` and `TELESCREEN_INTERNAL_DOMAINS` for domain-wide coverage. See the [worker configuration](../../../go/README.md) for all settings.

Create a Police collector configuration with a selector such as:

```json
{"name":"drive-police","kind":"police","selector":{"labels":["police=gdrive"],"input":{"kind":"gdrive","drive_ids":["0AExampleDriveId"]}}}
```

Start a sweep with `POST /api/collectors/{id}/sweep`. A workflow can instead use:

```yaml
- id: sweep_drive
  type: map
  source:
    partition:
      via: police
      collector: drive-police
      task_type: partition
  map:
    via: police
    task_type: scan_shard
  max_concurrency: 64
  shard_timeout: PT15M
  shard_max_attempts: 3
  timeout: P1D
```

Run multiple replicas with the same labels. Set `TELESCREEN_POLICE_CONCURRENCY` for each worker and `max_concurrency` to bound in-flight shards per job. Findings appear under `police:<config>`; running a built-in `gdrive` collector on the same estate double-reports. Review the G1-G5 contract gaps and V3 shard-wide failure consequence in the worker README before operating sweeps.
