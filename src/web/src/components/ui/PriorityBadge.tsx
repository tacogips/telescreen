import type { JSX } from "solid-js";
import { Tag } from "./Tag";
export function PriorityBadge(
  props: Readonly<{ level: "critical" | "high" | "medium" | "low" }>,
): JSX.Element {
  return (
    <Tag variant="muted">
      <svg
        aria-hidden="true"
        viewBox="0 0 20 16"
        class="priority-signal"
        classList={{
          critical: props.level === "critical",
          high: props.level === "high",
          medium: props.level === "medium",
          low: props.level === "low",
        }}
      >
        <rect x="2" y="10" width="2" height="4" />
        <rect x="7" y="6" width="2" height="8" />
        <rect x="12" y="2" width="2" height="12" />
        <rect x="17" y="0" width="2" height="14" />
      </svg>
      <span>{props.level}</span>
    </Tag>
  );
}
