import type { JSX } from "solid-js";
export function Placeholder(props: Readonly<{ label: string }>): JSX.Element {
  return <span class="placeholder">{props.label}</span>;
}
