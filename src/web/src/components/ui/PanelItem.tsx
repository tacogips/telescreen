import type { JSX } from "solid-js";
export function PanelItem(
  props: Readonly<{ children: JSX.Element }>,
): JSX.Element {
  return <div class="panel-item">{props.children}</div>;
}
