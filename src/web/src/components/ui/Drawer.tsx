import { Show, type JSX } from "solid-js";
import { Button } from "./Button";
import { readStorage, writeStorage } from "../shell/storage";
export function Drawer(
  props: Readonly<{
    open: boolean;
    onClose(): void;
    title: string;
    mode?: "blocking" | "passive";
    children: JSX.Element;
  }>,
): JSX.Element {
  const width = Math.min(
    900,
    Math.max(320, Number(readStorage("telescreen.drawerWidth")) || 560),
  );
  return (
    <Show when={props.open}>
      <div
        class="drawer-backdrop"
        onClick={props.mode === "passive" ? props.onClose : undefined}
      >
        <aside
          class="drawer"
          role="dialog"
          aria-modal={props.mode !== "passive"}
          aria-label={props.title}
          tabindex="-1"
          ref={(element) => queueMicrotask(() => element.focus())}
          style={{ width: `${width}px`, resize: "horizontal" }}
          onMouseUp={(event) =>
            writeStorage(
              "telescreen.drawerWidth",
              String(event.currentTarget.offsetWidth),
            )
          }
          onKeyDown={(event) => {
            if (event.key === "Escape") props.onClose();
            if (event.key === "Tab") {
              const focusable =
                event.currentTarget.querySelectorAll<HTMLElement>(
                  'button,a[href],input,select,textarea,[tabindex]:not([tabindex="-1"])',
                );
              const first = focusable.item(0);
              const last = focusable.item(focusable.length - 1);
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }
          }}
        >
          <header>
            {props.title}
            <Button size="xs" aria-label="Close" onClick={props.onClose}>
              ×
            </Button>
          </header>
          {props.children}
        </aside>
      </div>
    </Show>
  );
}
