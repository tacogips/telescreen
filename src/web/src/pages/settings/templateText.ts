/** Editor field model shared by the template list (create mode) and template detail. */
import { ApiError } from "../../api/client";
import type { TemplateVersionText } from "../../api/types";

export const EVENT_KINDS: readonly string[] = [
  "workflow_notify", "human_task_assigned", "owner_review_assigned", "sla_escalated", "owner_digest",
  "exception_requested", "exception_expired", "verification_inconclusive", "collector_health_alert", "police_health_alert",
];
export type TemplateField = "subject" | "body" | "email_subject" | "email_body" | "slack_body";
export const TEMPLATE_FIELDS: readonly Readonly<{ field: TemplateField; label: string; rows: number }>[] = [
  { field: "subject", label: "Subject", rows: 2 },
  { field: "body", label: "Body", rows: 6 },
  { field: "email_subject", label: "Email subject", rows: 2 },
  { field: "email_body", label: "Email body", rows: 6 },
  { field: "slack_body", label: "Slack body", rows: 4 },
];
export type TemplateDraft = Readonly<Record<TemplateField, string>>;
export const EMPTY_DRAFT: TemplateDraft = { subject: "", body: "", email_subject: "", email_body: "", slack_body: "" };

export function draftFromVersion(version: TemplateVersionText): TemplateDraft {
  return { subject: version.subject, body: version.body, email_subject: version.email_subject ?? "", email_body: version.email_body ?? "", slack_body: version.slack_body ?? "" };
}

/** Request text: optional channel variants are omitted when empty. */
export function draftPayload(draft: TemplateDraft): Readonly<{ subject: string; body: string; email_subject?: string; email_body?: string; slack_body?: string }> {
  return {
    subject: draft.subject, body: draft.body,
    ...(draft.email_subject.length > 0 ? { email_subject: draft.email_subject } : {}),
    ...(draft.email_body.length > 0 ? { email_body: draft.email_body } : {}),
    ...(draft.slack_body.length > 0 ? { slack_body: draft.slack_body } : {}),
  };
}

export function asApiError(cause: unknown, detail: string): ApiError {
  if (cause instanceof ApiError && cause.code === "raw_value_detected") return new ApiError(cause.status, cause.code, "Template text contains sensitive values.");
  return cause instanceof ApiError ? cause : new ApiError(0, "request_failed", detail);
}
