import type { JSX } from "solid-js";
import type { TagVariant } from "./types";
let toastTimer: number | undefined;
export function showToast(
  message: string,
  variant: TagVariant = "muted",
): void {
  const root = document.getElementById("toast-region");
  if (root === null) return;
  root.textContent = message;
  root.className = `toast-region ui-toast ${variant}`;
  if (toastTimer !== undefined) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    root.textContent = "";
  }, 4000);
}
export function Toast(): JSX.Element {
  return <div class="toast-region" aria-live="polite" />;
}
