import type { JSX } from "solid-js";

export function StatusBadge(props: Readonly<{ status: string }>): JSX.Element {
  const normalized = () => props.status.toLowerCase().replaceAll("_", "-");
  return <span class={`status-badge status-${normalized()}`}>{props.status.replaceAll("_", " ")}</span>;
}
