/** Demo-only v1.2 settings data. Values are synthetic and contain no credentials. */
import type { TokenSummary, UserSummary } from "../../types";
export type MockTemplateVersion = { name: string; version: number; event_kind: string; description: string | null; subject: string; body: string; email_subject?: string; email_body?: string; slack_body?: string; active: boolean };
export const mockTemplates: MockTemplateVersion[] = [
  ...["workflow_notify", "human_task_assigned", "owner_review_assigned", "sla_escalated", "owner_digest", "exception_requested", "exception_expired", "verification_inconclusive", "collector_health_alert", "police_health_alert"].map((event_kind) => ({ name: `system.${event_kind}`, version: 0, event_kind, description: "Built-in notification", subject: `${event_kind.replaceAll("_", " ")} update`, body: "A masked Telescreen notification is available.", active: true })),
  { name: "workflow.review", version: 1, event_kind: "workflow_notify", description: "Workflow review message", subject: "Review {{ issue.short_id }}", body: "{{ workflow.name }} needs review.", active: true },
  { name: "workflow.review", version: 2, event_kind: "workflow_notify", description: "Workflow review message", subject: "Review {{ issue.short_id }}", body: "{{ workflow.name }} needs your review.", active: false },
];
export const mockUsers: UserSummary[] = [
  { handle: "alex", display_name: "Alex Morgan", email: null, role: "admin", idp_managed: false, active: true, last_sign_in_at: null, teams: ["security"] },
  { handle: "riley", display_name: "Riley Chen", email: null, role: "responder", idp_managed: true, active: true, last_sign_in_at: "2026-10-10T12:00:00Z", teams: ["security"] },
  { handle: "sam", display_name: "Sam Lee", email: null, role: "viewer", idp_managed: false, active: true, last_sign_in_at: null, teams: [] },
  { handle: "drew", display_name: "Drew Park", email: null, role: null, idp_managed: true, active: true, last_sign_in_at: null, teams: [] },
];
export const mockTokens: TokenSummary[] = [{ id: "tok-01", label: "Daily review", scopes: ["read"], member_handle: "alex", created_at: "2026-10-01T00:00:00Z", revoked_at: null }];
export const mockRoleMatrix = { roles: [{ role: "admin", permissions: ["users.read", "users.write", "templates.read", "templates.write"] }, { role: "responder", permissions: ["users.read", "templates.read"] }, { role: "viewer", permissions: ["templates.read"] }], scopes: [{ scope: "read", permissions: ["templates.read"] }, { scope: "write", permissions: ["templates.write"] }, { scope: "admin", permissions: ["users.write"] }] };
