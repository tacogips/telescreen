import type { JSX } from "solid-js";
export function SeverityBar(
  props: Readonly<{ severity: "high" | "medium" | "low" }>,
): JSX.Element {
  return (
    <span
      class={`severity-bar ${props.severity}`}
      aria-label={`${props.severity} severity`}
    />
  );
}
