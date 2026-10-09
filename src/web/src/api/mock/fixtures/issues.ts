import type { Issue, IssueEvent, IssueHistory } from "../../types";

const LOAD_TIME = Date.now();
const ago = (minutes: number): string => new Date(LOAD_TIME - minutes * 60_000).toISOString();

export const issueFixtures: Issue[] = [
  { id: "iss-01", number: 1, short_id: "TELE-1", grouping_key: "grp-01", source: "github", resource_key: "repo-01", display_locator: "acme/api:config.py:12", resource_ref_json: null, collector_config_id: "col-01", display_title: "Credential in repository configuration", rule_class: "credential", severity: "high", status: "unresolved", assignee_member_id: "mem-01", assignee_team_id: null, first_seen: ago(300), last_seen: ago(8), last_checked_at: ago(8), sweep_covered: true, times_seen: 5, distinct_fingerprints: 1, regression_count: 0, resolved_at: null, resolution: null },
  { id: "iss-02", number: 2, short_id: "TELE-2", grouping_key: "grp-02", source: "slack", resource_key: "room-01", display_locator: "#general message 14", resource_ref_json: null, collector_config_id: "col-02", display_title: "Personal information in a message", rule_class: "pii", severity: "medium", status: "acknowledged", assignee_member_id: null, assignee_team_id: "team-01", first_seen: ago(600), last_seen: ago(30), last_checked_at: ago(30), sweep_covered: true, times_seen: 3, distinct_fingerprints: 1, regression_count: 0, resolved_at: null, resolution: null },
  { id: "iss-03", number: 3, short_id: "TELE-3", grouping_key: "grp-03", source: "gdrive", resource_key: "doc-01", display_locator: "Finance planning sheet", resource_ref_json: null, collector_config_id: "col-03", display_title: "Payment card in a spreadsheet", rule_class: "payment_card", severity: "high", status: "in_progress", assignee_member_id: "mem-02", assignee_team_id: null, first_seen: ago(900), last_seen: ago(50), last_checked_at: ago(50), sweep_covered: false, times_seen: 2, distinct_fingerprints: 1, regression_count: 0, resolved_at: null, resolution: null },
  { id: "iss-04", number: 4, short_id: "TELE-4", grouping_key: "grp-04", source: "gdocs", resource_key: "doc-02", display_locator: "Operations handbook section 3", resource_ref_json: null, collector_config_id: "col-04", display_title: "Private key in shared document", rule_class: "private_key", severity: "critical", status: "resolved", assignee_member_id: null, assignee_team_id: null, first_seen: ago(1900), last_seen: ago(1000), last_checked_at: ago(1000), sweep_covered: true, times_seen: 2, distinct_fingerprints: 1, regression_count: 0, resolved_at: ago(800), resolution: "removed" },
  { id: "iss-05", number: 5, short_id: "TELE-5", grouping_key: "grp-05", source: "gsheets", resource_key: "sheet-01", display_locator: "Hiring tracker row 6", resource_ref_json: null, collector_config_id: "col-03", display_title: "Email address in a sheet", rule_class: "pii", severity: "low", status: "ignored", assignee_member_id: null, assignee_team_id: null, first_seen: ago(2800), last_seen: ago(2200), last_checked_at: ago(2200), sweep_covered: true, times_seen: 1, distinct_fingerprints: 1, regression_count: 0, resolved_at: null, resolution: null },
  { id: "iss-06", number: 6, short_id: "TELE-6", grouping_key: "grp-06", source: "github", resource_key: "repo-02", display_locator: "acme/web:settings.ts:8", resource_ref_json: null, collector_config_id: "col-01", display_title: "Credential reappeared after resolution", rule_class: "credential", severity: "high", status: "regressed", assignee_member_id: null, assignee_team_id: null, first_seen: ago(3500), last_seen: ago(4), last_checked_at: ago(4), sweep_covered: false, times_seen: 4, distinct_fingerprints: 2, regression_count: 1, resolved_at: null, resolution: null },
];

export const eventFixtures: IssueEvent[] = issueFixtures.map((issue, index) => ({
  id: `evt-0${index + 1}`, issue_id: issue.id, scan_job_id: `scan-0${index + 1}`, source: issue.source,
  resource_key: issue.resource_key, display_locator: issue.display_locator, location: `line ${index + 1}`,
  rule_id: issue.rule_class, rule_class: issue.rule_class, detector: "rules", confidence: 0.98,
  masked_snippet: index === 0 ? "ghp_****************[40]" : "Sensitive value [REDACTED]",
  fingerprint: `fp-0${index + 1}`, fingerprint_key_id: "key-01", observed_at: issue.last_seen,
}));

export const historyFixtures: IssueHistory[] = issueFixtures.map((issue, index) => ({
  id: `hist-0${index + 1}`, issue_id: issue.id, kind: "status_changed", actor_ref: "member:alex",
  from_value: index === 3 ? "unresolved" : null, to_value: issue.status, note: "Reviewed masked finding", at: issue.last_seen,
}));

export const maskedExamples: string[] = [
  "ghp_****************[40]",
  "AKIA****************[20]",
  "********[24]",
  "**** **** **** 1111",
  "************",
  ["j***", "@example.com"].join(""),
  "***-****-**78",
  "[ADDRESS REDACTED]",
  "-----BEGIN PRIVATE KEY----- [REDACTED 1675 chars]",
];
