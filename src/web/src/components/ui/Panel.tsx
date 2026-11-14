import { Show, type JSX } from "solid-js";
export function Panel(
  props: Readonly<{ title?: string; children: JSX.Element }>,
): JSX.Element {
  return (
    <section class="ui-panel">
      <Show when={props.title}>
        <h2>{props.title}</h2>
      </Show>
      {props.children}
    </section>
  );
}
