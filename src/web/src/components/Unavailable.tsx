import type { JSX } from "solid-js";
import { TopBar } from "./shell/TopBar";
export function Unavailable(props: Readonly<{ label: string }>): JSX.Element {
  return <section class="unavailable"><TopBar.Slot name="title">{props.label}</TopBar.Slot><h1 class="visually-hidden">{props.label}</h1><p>This page is not available yet.</p></section>;
}
