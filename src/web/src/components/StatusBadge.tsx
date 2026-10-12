import type { JSX } from "solid-js";
import { TriangleAlert } from "lucide-solid";
import { Tag, type TagVariant } from "./ui";
export function StatusBadge(props: Readonly<{ status: string; resolution?: string | null }>): JSX.Element {
  const status = props.status.toLowerCase();
  const unverified = status === "resolved" && props.resolution === "manual_unverified";
  const variant: TagVariant = unverified ? "warning" : ["unresolved", "regressed", "failed", "rejected", "escalating"].includes(status) ? "danger" : ["acknowledged", "in_progress", "running", "waiting", "pending_verification"].includes(status) ? "info" : ["resolved", "succeeded", "accepted"].includes(status) ? "success" : status === "critical" ? "critical" : "muted";
  const label = unverified ? "Resolved (unverified)" : status === "allowed" ? "Allowed" : status === "pending_verification" ? "Verifying" : status === "archived" ? "Archived" : status === "escalating" ? "Escalating" : props.status.replaceAll("_", " ");
  return <span class={`status-badge status-${status.replaceAll("_", "-")}`}><Tag variant={variant}>{unverified && <TriangleAlert size={12} aria-label="Warning"/>}{label}</Tag></span>;
}
