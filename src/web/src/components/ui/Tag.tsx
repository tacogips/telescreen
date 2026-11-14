import type { JSX } from "solid-js";
import type { TagVariant } from "./types";
export function Tag(
  props: Readonly<{ variant?: TagVariant; children: JSX.Element }>,
): JSX.Element {
  return (
    <span class={`ui-tag ${props.variant ?? "muted"}`}>{props.children}</span>
  );
}
