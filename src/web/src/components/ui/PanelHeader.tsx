import type { JSX } from "solid-js";
export function PanelHeader(
  props: Readonly<{ children: JSX.Element }>,
): JSX.Element {
  return <header class="panel-header">{props.children}</header>;
}
