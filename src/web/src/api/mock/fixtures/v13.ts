import type { AlertFire, AlertRule, Issue, IssueStatus, IntegrationStatus, NotificationSetting, OwnershipRule, SelfToken, Visibility } from "../../types";
import { issueFixtures } from "./issues";

const statuses: readonly IssueStatus[] = ["unresolved", "acknowledged", "in_progress", "regressed", "escalating", "archived", "resolved", "ignored", "allowed", "pending_verification"];
const sources: readonly string[] = ["slack", "gdrive", "gdocs", "gsheets", "github", "custom", "police", "onedrive"];
const exposures: readonly Visibility[] = ["public", "anyone_with_link", "external_shared", "domain", "internal", "private", "unknown"];
const names = ["Alice Example", "Bob Example", "Casey Example", "Dana Example", "Evan Example"] as const;
const baseTime = Date.parse("2026-10-12T00:00:00.000Z");

export const v13Issues: Issue[] = Array.from({ length: 40 }, (_, index): Issue => {
  const base = issueFixtures[index % issueFixtures.length]!;
  const status = statuses[Math.floor(index / 2)]!;
  const seen = new Date(baseTime - (index * 7 + 2) * 3_600_000).toISOString();
  const member = index % 5;
  return {
    ...base,
    id: `v13-iss-${String(index + 1).padStart(2, "0")}`,
    number: index + 1,
    short_id: `TELE-${index + 1}`,
    grouping_key: index < 3 ? "v13-group-primary" : `v13-group-${index + 1}`,
    source: sources[index % sources.length]!,
    display_locator: `example.test/resource-${index + 1}`,
    display_title: `Masked finding ${index + 1}`,
    severity: (["critical", "high", "medium"] as const)[index % 3]!,
    status,
    assignee_member_id: index % 3 === 0 ? `mem-0${member + 1}` : null,
    assignee_team_id: index % 3 === 1 ? `team-0${(index % 2) + 1}` : null,
    exposure_visibility: exposures[index % exposures.length]!,
    first_seen: new Date(baseTime - (index * 7 + 14 * 24) * 3_600_000).toISOString(),
    last_seen: seen,
    last_checked_at: seen,
    times_seen: index + 1,
    distinct_fingerprints: index % 4 + 1,
    regression_count: status === "regressed" ? 1 : 0,
    resolved_at: status === "resolved" ? seen : null,
    resolution: status === "resolved" ? "false_positive" : null,
    priority: (["high", "medium", "low"] as const)[index % 3]!,
    priority_manual: index % 4 === 0,
    needs_review: index % 2 === 0,
    assignee: index % 3 === 0 ? { kind: "member", ref: `member:mem-0${member + 1}`, display_name: names[member]! } : index % 3 === 1 ? { kind: "team", ref: `team:team-0${(index % 2) + 1}`, display_name: index % 2 === 1 ? "Security" : "Engineering" } : null,
    archive: status === "archived" ? { kind: "for_duration", until: new Date(baseTime + 86_400_000).toISOString(), threshold: null, baseline: null } : null,
    merged_into_id: index === 1 || index === 2 ? "v13-iss-01" : null,
    merged_count: index === 0 ? 2 : 0,
    bookmarked: index % 7 === 0,
    updated_at: seen,
    trend: Array.from({ length: 14 }, (_, day) => (index + day) % 4),
  };
});

export const v13AlertRules: AlertRule[] = [
  { id: "alert-01", name: "New high priority", enabled: true, trigger: "issue_created", query: "priority:high", action_interval_minutes: 60, muted_until: null },
  { id: "alert-02", name: "Regressions", enabled: true, trigger: "issue_regressed", query: "is:regressed", action_interval_minutes: 30, muted_until: null },
  { id: "alert-03", name: "Values increase", enabled: false, trigger: "values", query: "values:>3", action_interval_minutes: 120, muted_until: null },
];
export const v13AlertFires: AlertFire[] = [
  { id: "fire-01", rule_id: "alert-01", issue_id: "v13-iss-01", outcome: "delivered", at: "2026-10-11T09:00:00Z" },
  { id: "fire-02", rule_id: "alert-02", issue_id: "v13-iss-08", outcome: "suppressed_interval", at: "2026-10-11T09:05:00Z" },
  { id: "fire-03", rule_id: "alert-03", issue_id: "v13-iss-14", outcome: "muted", at: "2026-10-11T09:10:00Z" },
];
export const v13Settings: NotificationSetting[] = ["assigned", "mentions", "subscribed", "alerts", "workflow_tasks", "weekly_report"].map((category) => ({ category, preference: "email" }));
export const v13Tokens: SelfToken[] = [{ id: "self-token-01", label: "Read only report", scopes: ["read"], preview: "ts_live_••••71a2", created_at: "2026-10-01T00:00:00Z" }];
export const v13OwnershipRules: OwnershipRule[] = Array.from({ length: 4 }, (_, index) => ({ id: `ownership-${index + 1}`, source: index === 0 ? "github" : null, pattern: `example.test/team-${index + 1}/*`, team_ref: index % 2 === 0 ? "team:engineering" : "team:security", position: index }));
export const v13Integrations: IntegrationStatus[] = [{ kind: "slack", configured: true, status: "ready" }, { kind: "email", configured: true, status: "ready" }, { kind: "github_issues", configured: false, status: "unconfigured" }, { kind: "oidc", configured: true, status: "ready" }];
