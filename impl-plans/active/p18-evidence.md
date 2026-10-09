# P18 Evidence Upload, Scan-and-Reject, Image Scanner and Metadata Stripper Implementation Plan

**Status**: Completed
**Design Reference**: design-docs/specs/design-api-and-ui.md (Evidence), design-docs/specs/command.md (Dependencies decisions: stdlib stripper), design-docs/user-qa/qa-evidence-file-types.md, design-docs/specs/design-masking.md (leak test matrix, image cases)
**Created**: 2026-10-08
**Last Updated**: 2026-10-08

## Plan Metadata

- planId: `p18-evidence`
- planPath: `impl-plans/active/p18-evidence.md`
- wave: 4
- dependsOn: `p08-masking`, `p09-detection-llm-ml`, `p10-api-common`
- writePaths:
  - `impl-plans/active/p18-evidence.md`
  - `src/telescreen/evidence`
  - `src/telescreen/api/routers/evidence.py`
  - `tests/evidence`
- sharedPaths: none
- artifactRoots: none

Read `impl-plans/conventions.md` first.

## Intent and Context

Acceptance: "evidence upload API with rejection on PII/credentials". Text evidence is
scanned with the configured text `Detector`. Image evidence (PNG, JPEG, WebP; owner
decision) is scanned by the vision-capable managed LLM and fails closed. Clean images are
stored only after their metadata is stripped. Rejected content is discarded. This
package also implements the `EvidenceQuery` port used by the workflow evidence gate.

## Non-goals

- No Pillow (stdlib-only stripper). No multipart (raw body only). Evidence does not
  signal runs; completion happens through human tasks (p24).

## Deliverables (`src/telescreen/evidence/`)

- `types.py`: `EvidenceKind` (text, image);
  `classify(filename: str, data: bytes) -> tuple[EvidenceKind, str]` (kind and content
  type).
  - Text extensions are `.txt .md .log .csv .json .yaml .yml`, and the data must decode
    as UTF-8.
  - Images are `.png` (magic `\x89PNG\r\n\x1a\n`), `.jpg`/`.jpeg` (`\xff\xd8\xff`) and
    `.webp` (`RIFF....WEBP`). The extension and the magic bytes must agree.
  - Anything else -> `TelescreenError(unsupported_media_type)`.
- `strip.py`: `strip_metadata(kind_content_type: str, data: bytes) -> bytes`, a container
  walk without pixel decoding:
  - JPEG: remove APP1-APP15 and COM segments, keep APP0 and everything from SOS on;
  - PNG: drop the `tEXt`, `iTXt`, `zTXt`, `eXIf` and `tIME` chunks and keep CRCs of the
    others intact;
  - WebP: drop the `EXIF` and `XMP ` chunks, clear the VP8X EXIF (0x08) and XMP (0x04)
    flags, and fix the RIFF size.
  - Any malformed structure -> `TelescreenError(evidence_rejected)` with reason
    `malformed_image` (put the reason code in the rejection rule-classes list as
    `malformed_image`).
- `image.py`:
  - `ImageVerdict(contains_sensitive: bool, rule_classes: tuple[RuleClass, ...])`.
  - `class ImageEvidenceScanner(Protocol): async def scan(self, content_type: str, data: bytes) -> ImageVerdict`.
  - `LlmImageEvidenceScanner(client: LlmClient, model: str)`: an `LlmJsonRequest` with
    one `LlmImagePart`. The system prompt says "never transcribe content; answer only
    the JSON schema". The schema is `{contains_sensitive: boolean, rule_classes: [enum]}`.
    The response is parsed strictly with `TypeAdapter`; anything else (free text, extra
    keys) -> rejection with reason `scanner_invalid_output`. Provider errors after 2
    retries -> rejection (fail closed).
- `store.py`: `class EvidenceStore(Protocol)` with `put(key, data)`, `get(key) -> bytes`;
  `LocalFileEvidenceStore(root: Path)` writes `<root>/<key>` with mode 0600 and creates
  the directory.
- `service.py`: `EvidenceService(sessions, detector: Detector, image_scanner: ImageEvidenceScanner | None, store: EvidenceStore, issues: IssueRecorder, redactor: Redactor, audit: AuditLog, clock, max_bytes: int)`
  with `upload(issue_id, step_run_id: str | None, filename: str, data: bytes, actor_ref) -> EvidenceRow`:
  1. Classify the content.
  2. Text: detector scan; any finding -> reject.
  3. Image: no scanner -> `TelescreenError(evidence_scanner_unavailable)` (503) with no
     row stored. Otherwise scan, then reject on `contains_sensitive`; else strip the
     metadata.
  4. Reject means: insert an `evidence` row with verdict `rejected` and the rule
     classes, no sha256 and no storage key, the redacted filename, and audit
     `evidence.rejected`; then raise `TelescreenError(evidence_rejected)`. The router
     returns 422 with the rule classes only.
  5. Accept means: store the stripped bytes under key = evidence id, set the sha256 of
     the stored bytes, `add_history(evidence_added)` through `IssueRecorder`, and audit
     `evidence.accepted`.
  Also `list(issue_id, step_run_id)`, `get(id)` and `content(id) -> bytes`.
- `query.py`: `EvidenceQueryImpl` implements `EvidenceQuery.accepted_count`, counting
  rows with verdict `accepted`, a matching `step_run_id` AND a matching `issue_id`.

### Router `routers/evidence.py`
- `EvidenceRouterDeps(ctx: ApiContext, service: EvidenceService, max_bytes: int)`.
- `PUT /api/evidence?issue_id&step_run_id&filename` (write): the raw body through
  `read_limited_body`; 413 when it is too large.
- Rejections answer 422 with
  `{"error":{"code":"evidence_rejected","detail":"evidence rejected","rule_classes":[...]}}`.
  This is a dedicated response from this router, not the generic handler, so that rule
  classes can be added.
- `GET /api/evidence?issue_id&step_run_id`, `GET /api/evidence/{id}`,
  `GET /api/evidence/{id}/content` (read; `application/octet-stream`; only accepted
  rows).

## Key Pitfalls

- Never write unscanned bytes to disk; there is no temp spooling.
- `display_filename` is the redacted filename; the stored key is the evidence id, never
  the filename.
- Do not log filenames or content.
- The PNG chunk walk must verify lengths and stop at `IEND`. Any truncation means
  malformed.

## Tests (`tests/evidence/`)

Build test images in code: a minimal valid PNG (IHDR, IDAT and IEND via zlib), a minimal
JPEG byte sequence (SOI, APP0, APP1 with an EXIF payload, COM, SOS, data, EOI) and a WebP
VP8X with EXIF and XMP chunks. Embed sample secrets in the metadata.

- Text upload with a sample secret -> 422 `evidence_rejected` with rule classes; the row
  is rejected; no file under the store root; the response has no leak.
- Clean text -> accepted; the file is stored; `history evidence_added` is recorded
  through the fake recorder.
- PNG and JPEG with secrets in `tEXt` or EXIF and a fake scanner returning clean ->
  accepted; the stored bytes contain no sample (`find_leaks` on
  `bytes.decode("latin-1")`); the stripped PNG still has a valid chunk sequence.
- WebP with EXIF and XMP -> stripped, flags cleared, RIFF size correct.
- The fake scanner reports sensitive -> rejected, nothing stored.
- No scanner -> 503 `evidence_scanner_unavailable`, no row.
- `LlmImageEvidenceScanner` with a fake client returning free text -> rejected.
- A `.png` name with JPEG magic -> 415. A `.exe` -> 415. A truncated PNG -> rejected
  `malformed_image`.
- `accepted_count` counts only rows with the matching issue and step run.
- Over-size body -> 413.

## Verification

```bash
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1
P="src/telescreen/evidence src/telescreen/api/routers/evidence.py tests/evidence"
uv run --frozen ruff format --check --no-cache $P
uv run --frozen ruff check --no-cache $P
uv run --frozen ty check $P
uv run --frozen pytest -p no:cacheprovider tests/evidence 2>&1 | tee /tmp/telescreen-p18-evidence-pytest.log
uv run --frozen python tools/typelint.py > /tmp/telescreen-p18-evidence-typelint.log 2>&1; grep -E '^(src/telescreen/evidence|src/telescreen/api/routers/evidence.py|tests/evidence)' /tmp/telescreen-p18-evidence-typelint.log   # prints nothing
```

## Run A Resume (2026-10-09, session 338)

State: passed per-branch test-integrity and adversarial review at s319; integration review
pending. Dispatch: `impl-plans/active/telescreen-v1-20261009-s338-dispatch.json`, wave A1.
Resume from the in-tree code; do not rewrite working modules. The static gap review found
no source defect in scope, only test coverage gaps against the Tests section:

- [x] R0 Baseline: run the Verification block on the untouched tree; corrected baseline
  logs are `/tmp/telescreen-p18-evidence-baseline-{format,ruff,ty,pytest,typelint}-correct.log`.
- [x] R1 Stripped PNG: walk the chunks of the stored bytes (length, type, CRC32 of
  type+data) through `IEND`; every CRC matches and no `tEXt`/`iTXt`/`zTXt`/`eXIf` chunk
  remains.
- [x] R2 Truncated PNG uploaded through `EvidenceService.upload` -> `evidence_rejected`;
  the rejected row's rule classes include `malformed_image`; nothing under the store root.
- [x] R3 HTTP-level 415: through the router (in-process ASGI app), a `.png` filename with
  JPEG magic -> 415 and an `.exe` -> 415; neither response echoes the filename or body.
- [x] R4 WebP through `upload` with a clean fake scanner: put a sample secret in the
  EXIF/XMP fixture; the stored bytes have no leak (`find_leaks` on latin-1 decode), VP8X
  flags cleared, RIFF size correct.
- [x] R5 Accepted plan deviation (no code change): `EvidenceQueryImpl.accepted_count`
  takes `session` first, matching the `EvidenceQuery` port in `src/telescreen/ports`.
  Confirmed against `src/telescreen/ports/evidence.py`; see the Progress Log.
- [x] R6 Re-run the full Verification block; every command exits 0; log paths recorded.

Out of scope here (not accepted requirements): async file I/O in the store and orphan-file
cleanup after a failed commit (the stored bytes are scanned and stripped, so no leak).

## Completion Criteria

- [x] Classifier, stripper (JPEG, PNG, WebP), image scanner, store, service, query and router delivered with the exact names
- [x] Every evidence case in the design-masking leak matrix is covered by a test here (after R1-R4)
- [x] Run A Resume items R0-R6 checked with evidence
- [x] Scoped verification passes on the final tree; logs recorded
- [x] Progress Log updated (run A session)

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan

### Session: 2026-10-09 p18 implementation
**Tasks Completed**: Implemented the evidence classifier, JPEG/PNG/WebP metadata stripping, fail-closed LLM image scanner, owner-only local store, evidence service/query, authenticated API router, and leak-focused tests. API tests cover rejection, bounded uploads, scanner-unavailable 503, list/get/content; storage tests assert mode 0600.
**Tasks In Progress**: None
**Blockers**: None within p18. Full-tree typelint reports violations outside p18 paths; p18 paths are clean.
**Verification**: `uv run --frozen ruff format --check --no-cache src/telescreen/evidence src/telescreen/api/routers/evidence.py tests/evidence` exit 0 (`/tmp/telescreen-p18-evidence-format-final.log`); `uv run --frozen ruff check --no-cache src/telescreen/evidence src/telescreen/api/routers/evidence.py tests/evidence` exit 0 (`/tmp/telescreen-p18-evidence-ruff-final.log`); `uv run --frozen ty check src/telescreen/evidence src/telescreen/api/routers/evidence.py tests/evidence` exit 0 (`/tmp/telescreen-p18-evidence-ty-final.log`); `uv run --frozen python tools/typelint.py src/telescreen/evidence src/telescreen/api/routers/evidence.py tests/evidence` exit 0, 9 files clean (`/tmp/telescreen-p18-evidence-typelint-scoped-final.log`); scoped pytest exit 0, 7 passed (`/tmp/telescreen-p18-evidence-pytest-final.log`). Full-tree typelint exit 1 with no p18 diagnostics (`/tmp/telescreen-p18-evidence-typelint-full-final.log`); this is not claimed as a global pass.
**Source Hashes**: `src/telescreen/evidence/__init__.py` 1b2cf1b99ed6594229976244220c89dad7ea65e5495c93466f0cd622092a5e0d; `image.py` 4cecab5e2bedcf15607a34abb8cd460067fb55358b87320c0c45d0a78e479f28; `query.py` 29f2119cae7aa02e268c1a179d33e04cd242f6e2dedaa7d1e12c54025a5c80f0; `service.py` 0330d2749bac5f87947292e7bbae7a328d560a7fe312185eb5909ba3556e8a28; `store.py` 4fb981d3e75e2f307a55c5d9f4f837ce45e6d62fbecbcb1a84def486900a8ad4; `strip.py` 25877e24a2a978a28a814bd408ad7d16482fea4281bbb25ce925d9d29b32b1cd; `types.py` d5bb07993248475fa0319c07a01f1e5791322534e6fab78aa49a58d58e709b4d; `src/telescreen/api/routers/evidence.py` 22fdcc9df30b05b93fce57aa5fcb0853bc5756b4d1035890ab96132d0adfd40e; `tests/evidence/test_evidence.py` 1bfa29e6bc9e557c9dfef46241ceb3aa3fd87d3a51c3b360e64e4c4510f52062.

### Session: 2026-10-09 p18 resume, session 338
**Tasks Completed**: Added stored-PNG chunk length/CRC/IEND and metadata assertions; service-level truncated-PNG rejection-row/no-storage coverage; router-level non-echoing 415 cases for mismatched and unsupported media; and accepted WebP upload coverage with runtime sample metadata secrets, stripped EXIF/XMP, cleared VP8X flags and corrected RIFF size. Confirmed `EvidenceQueryImpl.accepted_count(session, ...)` matches the `EvidenceQuery` port. All R0-R6 and completion criteria are checked.
**Tasks In Progress**: None.
**Blockers**: None.
**Reconciliation requests**: None.
**Hashes**: `tests/evidence/test_evidence.py` before=1bfa29e6bc9e557c9dfef46241ceb3aa3fd87d3a51c3b360e64e4c4510f52062 after=5d3011e81f59e57feaa379df3b71987a96271901019825ec23755db2bba863f; `impl-plans/active/p18-evidence.md` before=a0e75c73ac3e907d1b82026fa50e88dac41547ff37c09ea5ad102c51c69f8119 (recorded immediately before this session's plan update; previous implementation log remains unchanged).
**Verification**: Corrected untouched-tree baseline: format, Ruff, ty, pytest and p18 typelint filter all exit 0; logs `/tmp/telescreen-p18-evidence-baseline-{format,ruff,ty,pytest,typelint}-correct.log` (baseline pytest 7 passed). Final `uv run --frozen ruff format --check --no-cache src/telescreen/evidence src/telescreen/api/routers/evidence.py tests/evidence` -> exit 0 (`/tmp/telescreen-p18-evidence-format-final.log`); `uv run --frozen ruff check --no-cache src/telescreen/evidence src/telescreen/api/routers/evidence.py tests/evidence` -> exit 0 (`/tmp/telescreen-p18-evidence-ruff-final.log`); `uv run --frozen ty check src/telescreen/evidence src/telescreen/api/routers/evidence.py tests/evidence` -> exit 0 (`/tmp/telescreen-p18-evidence-ty-final.log`); `uv run --frozen pytest -p no:cacheprovider tests/evidence` -> exit 0, 9 passed (`/tmp/telescreen-p18-evidence-pytest-final.log`); `uv run --frozen python tools/typelint.py` -> exit 0 and no p18 diagnostics (`/tmp/telescreen-p18-evidence-typelint-final.log`).
**Notes**: The first baseline attempt incorrectly quoted the multi-path variable as one path and produced tool usage errors; corrected untouched-tree baseline passed before edits. Other workers have concurrent changes outside p18 writePaths; no such files were edited.
