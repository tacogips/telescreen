# P18 Evidence Upload, Scan-and-Reject, Image Scanner and Metadata Stripper Implementation Plan

**Status**: Ready
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

## Completion Criteria

- [ ] Classifier, stripper (JPEG, PNG, WebP), image scanner, store, service, query and router delivered with the exact names
- [ ] Every evidence case in the design-masking leak matrix is covered by a test here
- [ ] Scoped verification passes; logs recorded
- [ ] Progress Log updated

## Progress Log

### Session: 2026-10-08 00:00
**Tasks Completed**: Plan created
**Tasks In Progress**: None
**Blockers**: None
**Notes**: Initial plan
