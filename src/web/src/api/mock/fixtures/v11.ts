import type { CoverageResource, CoverageSummary, Issue, IssueException, Occurrence, PolicySummary, PolicyVersion, Visibility } from "../../types";
import { issueFixtures } from "./issues";

// Mock mode treats its built-in token as admin for exception workflow coverage.
const observedAt = "2026-10-10T00:00:00.000Z";
const visibilities: readonly Visibility[] = ["public", "anyone_with_link", "external_shared", "domain", "internal", "private", "unknown"];

export const v11Issues: Issue[] = [
  ...issueFixtures.map((issue, index): Issue => ({
    ...issue,
    severity: issue.severity,
    base_severity: issue.severity,
    exposure_visibility: visibilities[index % visibilities.length]!,
    risk_score: 5 + index * 13,
    policy_name: index % 2 === 0 ? "External data policy" : null,
    policy_version: index % 2 === 0 ? 1 : null,
  })),
  { ...issueFixtures[0]!, id: "iss-07", number: 7, short_id: "TELE-7", status: "allowed", severity: "medium", resolution: null, exposure_visibility: "external_shared", base_severity: "medium", risk_score: 31, policy_name: "External data policy", policy_version: 1 },
  { ...issueFixtures[1]!, id: "iss-08", number: 8, short_id: "TELE-8", status: "pending_verification", severity: "critical", base_severity: "high", resolution: null, exposure_visibility: "public", risk_score: 95, policy_name: null, policy_version: null },
  { ...issueFixtures[2]!, id: "iss-09", number: 9, short_id: "TELE-9", status: "resolved", severity: "high", base_severity: "high", resolution: "manual_unverified", exposure_visibility: "private", risk_score: 48, policy_name: null, policy_version: null },
];

export const occurrenceFixtures: Occurrence[] = [
  { issue_id: "iss-01", short_id: "TELE-1", source: "github", display_locator: "masked repository location", status: "unresolved", severity: "high", exposure_visibility: "public" },
  { issue_id: "iss-06", short_id: "TELE-6", source: "github", display_locator: "masked repository location", status: "regressed", severity: "high", exposure_visibility: "domain" },
];

export const exceptionFixtures: IssueException[] = [
  { id: "exc-01", issue_id: "iss-02", status: "requested", reason: "Temporary review request", duration: "7d", requested_by: "member:alex", decided_by: null, approver_ref: "member:alex", expires_at: null, created_at: observedAt, decided_at: null },
];

export const policyFixtures: PolicySummary[] = [
  { name: "External data policy", active_version: 1, effect: "violate", enabled: true, owner_team: "team:response" },
];
export const policyVersionFixtures: PolicyVersion[] = [
  { name: "External data policy", version: 1, source_format: "yaml", source_text: "name: External data policy\neffect: violate", active: true },
];
export const coverageSummaryFixture: CoverageSummary = {
  since: observedAt,
  counts: [{ state: "scanned", reason: null, count: 12 }, { state: "fetch_failed", reason: "permission_denied", count: 2 }],
  unscannable_total: 2,
};
export const coverageResourceFixtures: CoverageResource[] = [
  { resource_id: "a1b2c3d4e5f6", source: "gdrive", state: "fetch_failed", reason: "permission_denied", truncated_bytes: 0, exposure_visibility: "public", first_seen_at: observedAt, last_attempt_at: observedAt, issues: [{ issue_id: "iss-08", short_id: "TELE-8" }] },
];
