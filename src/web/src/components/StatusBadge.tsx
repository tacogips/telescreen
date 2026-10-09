import type { JSX } from "solid-js";
import { Tag, type TagVariant } from "./ui";
export function StatusBadge(props: Readonly<{ status: string }>): JSX.Element {
  const status = props.status.toLowerCase();
  const variant: TagVariant = ["unresolved", "regressed", "failed", "rejected"].includes(status) ? "danger" : ["acknowledged", "in_progress", "running", "waiting"].includes(status) ? "info" : ["resolved", "succeeded", "accepted"].includes(status) ? "success" : "muted";
  return <span class={`status-badge status-${status.replaceAll("_", "-")}`}><Tag variant={variant}>{props.status.replaceAll("_", " ")}</Tag></span>;
}
