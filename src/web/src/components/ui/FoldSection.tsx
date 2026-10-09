import { Show, createSignal, type JSX } from "solid-js";
import { readStorage, writeStorage } from "../shell/storage";
export function FoldSection(
  props: Readonly<{ id: string; title: string; children: JSX.Element }>,
): JSX.Element {
  const key = `telescreen.fold.${props.id}`;
  const [open, setOpen] = createSignal(
    readStorage(key) !== "false" || location.hash === `#${props.id}`,
  );
  return (
    <section id={props.id} class="fold-section">
      <button
        aria-expanded={open()}
        onClick={() => {
          const next = !open();
          setOpen(next);
          writeStorage(key, String(next));
        }}
      >
        {props.title}
      </button>
      <Show when={open()}>{props.children}</Show>
    </section>
  );
}
