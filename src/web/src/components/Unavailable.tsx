import type { JSX } from "solid-js";

export function Unavailable(props: Readonly<{ label: string }>): JSX.Element {
  return <section class="unavailable"><h1>{props.label}</h1><p>This page is not available yet.</p></section>;
}
