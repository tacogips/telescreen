import type { JSX } from "solid-js";
export function Timeline(
  props: Readonly<{ children: JSX.Element }>,
): JSX.Element {
  return <ol class="timeline">{props.children}</ol>;
}
