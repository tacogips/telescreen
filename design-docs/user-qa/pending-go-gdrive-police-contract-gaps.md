# Pending: Go Drive Police contract 1.2 gaps and scope defaults

Status: pending owner review. The defaults below apply until the owner decides otherwise.
Source: `design-docs/specs/design-go-gdrive-police.md`, sections 13 and 14.

The Go worker must conform to Police contract 1.2 as published, so no schema changes are
made. The following gaps change what a Police-path Drive scan can report compared with the
built-in Python collector.

| Id | Question | Default applied |
|----|----------|-----------------|
| G1 | The contract has no "checked but not fully scanned" channel (`resources_skipped`, coverage). How should oversized files be reported? | Count them in `resources_failed`. Sweeps over estates with text files above `TELESCREEN_SCAN_MAX_BYTES` end `partial` and do not reconcile. The cost is shard-wide, under unchanged Bureau rules: for any shard with `resources_failed > 0` the Bureau discards `complete_locators` (`src/telescreen/police/results.py:259-260`). One oversized or failed file therefore leaves up to `TELESCREEN_SWEEP_SHARD_SIZE - 1` other files (default 99) in its shard unrecorded as complete. The finding-free ones lose their exposure and inventory for that sweep, and the finding-bearing ones are recorded as incomplete (`src/telescreen/issues/external_scan.py:112`). The same applies to G3 guard moves. Unsupported-type files (images, binaries) are reported complete without findings and do not trigger this |
| G2 | Owner identity, placement and modified time cannot be sent | Omit them. Owner routing and placement policies do not apply to `police:<config>` sources |
| G3 | Results are capped at a 1 MiB body with no multi-part submission | Resources that do not fit move to `resources_failed`; findings are never truncated on a complete resource |
| G4 | The Bureau `Redactor` scans hex fingerprints, and `phone_jp` and `my_number` can match digit runs inside them. This affects the Python SDK too | The Go worker uses a deterministic clean re-derivation for flagged finding fingerprints. The SDK and the Bureau are unchanged |
| G5 | `complete_locators` allows at most 1000 entries | `detect` scans at most 1000 files from the selector's drives and folders; domain coverage uses sweeps |
| D6 | The workflow input lists the Drive changes feed in the client scope, but no Police task type uses a change cursor | The changes feed and watch are not ported (no dead code); they can be added if a consumer appears |
| V1 | The workflow input maps HTTP 410 to "resource gone"; Python maps only 404 | Owner decision (2026-10-30): follow Python. Only 404 means gone; 410 is a fetch failure (counted in `resources_failed`). The workflow input was wrong on this point |

A future contract minor version (1.3) could close G1 to G4 additively. That is out of
scope for this work.
