import { createSignal, Show, type JSX } from "solid-js";
import { Button } from "./Button";
export function Menu(
  props: Readonly<{ label: string; children: JSX.Element }>,
): JSX.Element {
  const [open, setOpen] = createSignal(false);
  return (
    <div class="ui-menu">
      <Button aria-expanded={open()} onClick={() => setOpen(!open())}>
        {props.label}
      </Button>
      <Show when={open()}>
        <div role="menu">{props.children}</div>
      </Show>
    </div>
  );
}
