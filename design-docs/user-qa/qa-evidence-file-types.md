# Evidence Upload File Types

**Status**: Resolved (2026-10-08, decided by the repository owner)

**Created**: 2026-10-08

**Category**: Architecture

## Decision Needed

Which evidence file types should Evidence accept?

## Background

Evidence must be scanned and rejected if it contains personal data or credentials. Content
that cannot be scanned (images, PDFs, office files) cannot meet that guarantee without text
extraction or OCR.

## Alternatives

### Option A: Text-like files only (v1 default)

- `.txt`, `.md`, `.log`, `.csv`, `.json`, `.yaml`, `.yml`, UTF-8 only.
- Screenshots and PDFs are rejected with `unsupported_media_type`.

### Option B: Add PDF text extraction

- Adds a PDF parsing dependency; scanned PDFs without a text layer still cannot be checked.

### Option C: Add image OCR

- Adds a heavy OCR dependency and lower detection reliability.

## Impact

`design-docs/specs/design-api-and-ui.md` (Evidence), UI upload hints.

## Decision

Accept text AND images. Text-like files are scanned by the text detector; images (`.png`, `.jpg`/`.jpeg`, `.webp`) are scanned by the vision-capable managed LLM (default provider Claude) via `ImageEvidenceScanner`, fail closed when unavailable, metadata stripped. PDF/office files remain unsupported in v1. See `design-docs/specs/design-api-and-ui.md` (Evidence).
