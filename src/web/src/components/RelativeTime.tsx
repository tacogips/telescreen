import type { JSX } from "solid-js";

export function RelativeTime(props: Readonly<{ iso: string }>): JSX.Element {
  const date = new Date(props.iso);
  const valid = Number.isFinite(date.getTime());
  return <time datetime={valid ? date.toISOString() : undefined}>{valid ? date.toLocaleString() : "Unknown time"}</time>;
}
