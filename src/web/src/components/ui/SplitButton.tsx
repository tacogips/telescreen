import { createSignal, Show, type JSX } from "solid-js";
import { Button } from "./Button";
import type { ButtonVariant } from "./types";
export function SplitButton(
  props: Readonly<{
    children: JSX.Element;
    onClick?: () => void;
    menuLabel?: string;
    menu: JSX.Element;
    variant?: ButtonVariant;
  }>,
): JSX.Element {
  const [open, setOpen] = createSignal(false);
  const close = (): void => {
    setOpen(false);
  };
  return (
    <div
      class="split-button"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          close();
        }
      }}
    >
      <Button variant={props.variant ?? "primary"} onClick={props.onClick}>
        {props.children}
      </Button>
      <Button
        variant={props.variant ?? "primary"}
        aria-label={props.menuLabel ?? "More actions"}
        aria-haspopup="menu"
        aria-expanded={open()}
        onClick={() => setOpen(!open())}
      >
        <svg aria-hidden="true" viewBox="0 0 16 16" class="split-chevron">
          <path d="m3 6 5 5 5-5" />
        </svg>
      </Button>
      <Show when={open()}>
        <div class="split-menu" role="menu" onClick={close}>
          {props.menu}
        </div>
      </Show>
    </div>
  );
}
