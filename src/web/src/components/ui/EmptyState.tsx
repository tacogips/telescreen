import type { JSX } from "solid-js";
export function EmptyState(
  props: Readonly<{ title: string; children?: JSX.Element }>,
): JSX.Element {
  return (
    <div class="empty-state">
      <h2>{props.title}</h2>
      {props.children}
    </div>
  );
}
