# Synthetic Test Dataset

Labeled, fictitious personal-data and credential corpus used to test and evaluate every
detector backend, the masking pipeline, the index guard, the outbound DLP gate and the leak
tests. Owner decision (2026-10-08): the resources listed below are the project's test
dataset sources.

## Overview

Telescreen needs test data that looks like real credentials and Japanese/English personal
data, carries exact ground-truth labels, and refers to no real person, account or place.
No single public dataset covers Japanese PII, credentials and source-shaped documents
(Slack messages, Sheets cells, Docs text, GitHub diffs) together, so the dataset is
generated from a seed by a project generator that combines the sources below.

Status: approved for use; the generator is a follow-up plan after the v1 implementation run
(it extends `tests/support/samples.py`, which is owned by the accepted p01-foundation plan).

## Details

### Approved dataset sources

| Source | Use in Telescreen | Notes |
|--------|-------------------|-------|
| `tests/support/samples.py` (in repo) | Canonical minimal sample set (14 rule classes) and `find_leaks()` for leak assertions | Values assembled from fragments at runtime (gitleaks-safe). Keep as the baseline; see "Known issues" |
| ai4privacy pii-masking-300k / 400k (Hugging Face) | English and European-language PII recall evaluation with span labels | No Japanese and no credentials; used for detector evaluation only, not unit tests |
| Nemotron-Personas-Japan (NVIDIA, Hugging Face, CC BY 4.0) | Realistic Japanese names, demographics and profile text for templates | Not labeled for detection; values are synthetic personas. Attribution required (CC BY 4.0) |
| presidio-evaluator (Microsoft Presidio research) | Pattern for template-based sentence generation with exact span labels | Use the approach (template + generated values = ground truth); dependency optional |
| Faker `ja_JP` / `en_US` | Filler names, companies, phone-shaped and address-shaped values | Faker values can coincide with real ones, so Faker output is never used for values that must be guaranteed fictitious (see rules) |

External datasets are downloaded on demand into a gitignored cache (`tmp/datasets/`) by an
evaluation command; they are never committed and never required by the offline
`uv run pytest` suite.

### Value rules (guaranteed fictitious)

| Class | Rule |
|-------|------|
| Email | Reserved domains only: `example.com`, `example.org`, `example.net`, `*.invalid` (RFC 2606 / RFC 6761) |
| AWS keys | AWS documentation example keys (`...EXAMPLE` suffixes) or generated values with an `EXAMPLE` marker |
| Other tokens (GitHub, Slack, Google, JWT, PEM) | Format-valid but obviously synthetic bodies (sequential alphabet, repeated markers) |
| Payment cards | Published card-network test numbers (for example `4111 1111 1111 1111`) and generated Luhn-valid numbers from test ranges |
| My Number | Generated 11-digit bodies with a correct check digit from an obviously synthetic pattern (for example ascending digits); never sampled from real-looking distributions |
| Phone (JP) | No official fictional range is known; use formats with an unassigned or test-only prefix agreed in the generator, and label them as synthetic |
| Address (JP) | Real prefecture/city names with impossible block numbers (for example `99999-99`) or fictional town names, so no value matches a real address |
| Names | Nemotron-Personas-Japan or Faker names, always combined with fictitious contact data above |

### Corpus shape

- Per-source templates: Slack message, Sheets cell grid, Docs paragraph, Drive text file,
  GitHub diff hunk, Police / n8n payload, evidence upload text.
- Every generated document carries ground-truth spans: `(start, end, rule_class)`.
- Hard negatives are included: order numbers shaped like My Number, UUIDs and hashes
  shaped like keys, example-domain URLs, 12-digit invoice ids, masked values (`****1111`).
- Generation is deterministic from a seed; tests build the corpus at runtime and never
  commit raw generated files.

### Uses

| Use | Consumer |
|-----|----------|
| Unit and leak tests | `tests/support/samples.py` (baseline) and the generated corpus |
| Detector evaluation (precision/recall per rule class) | A `telescreen eval detectors --dataset <name>` command comparing `rule`, `ml`, `managed_llm` (claude/codex) and `local_llm` on the same corpus |
| Map-reduce sweep tests | Fake Drive/GitHub/Slack estates populated from the corpus |
| Index guard / DLP gate tests | Documents that must be blocked or masked |

### Known issues in the current baseline samples

`tests/support/samples.py` was written before these rules. The follow-up plan replaces:

- `jp_address` (`東京都千代田区千代田1-1`): a real public address; use an impossible block
  number instead.
- `jp_mobile` (`09012345678`) and `jp_landline` (`0312345678`): possibly assignable real
  numbers; replace per the phone rule above.

## References

See `design-docs/references/README.md` (Test datasets section).
