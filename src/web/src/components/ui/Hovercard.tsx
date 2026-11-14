import type { JSX } from "solid-js";
export function Hovercard(
  props: Readonly<{ label: string; children: JSX.Element }>,
): JSX.Element {
  return (
    <span class="hovercard" tabIndex={0}>
      {props.children}
      <span role="tooltip">{props.label}</span>
    </span>
  );
}
