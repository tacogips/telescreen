import type { JSX } from "solid-js";
export function Tooltip(
  props: Readonly<{ label: string; children: JSX.Element }>,
): JSX.Element {
  return <span title={props.label}>{props.children}</span>;
}
