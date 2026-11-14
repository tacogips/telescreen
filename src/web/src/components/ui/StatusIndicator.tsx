import type { JSX } from "solid-js";
export function StatusIndicator(
  props: Readonly<{ status: string; label?: string }>,
): JSX.Element {
  return (
    <span class={`status-indicator ${props.status}`}>
      <i />
      {props.label ?? props.status}
    </span>
  );
}
