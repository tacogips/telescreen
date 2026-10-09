import type { JSX } from "solid-js";
export function PageFilterBar(
  props: Readonly<{ children: JSX.Element }>,
): JSX.Element {
  return <div class="page-filter-bar">{props.children}</div>;
}
