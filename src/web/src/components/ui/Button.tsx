import type { JSX } from "solid-js";
import type { ButtonVariant } from "./types";
export function Button(
  props: JSX.ButtonHTMLAttributes<HTMLButtonElement> &
    Readonly<{
      variant?: ButtonVariant;
      size?: "md" | "sm" | "xs" | "zero";
      busy?: boolean;
    }>,
): JSX.Element {
  return (
    <button
      {...props}
      class={`ui-button ${props.variant ?? "default"} size-${props.size ?? "md"} ${props.class ?? ""}`}
      aria-busy={props.busy || undefined}
      disabled={props.disabled || props.busy}
    >
      <span class="button-label">{props.busy ? "…" : props.children}</span>
    </button>
  );
}
